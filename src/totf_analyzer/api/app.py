"""Local HTTP API for session analytics."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from totf_analyzer.analytics.metrics import build_analytics
from totf_analyzer.models.analytics import FightAnalytics
from totf_analyzer.parser.session import COLOR_CORNER, load_session

# Proton prefix for Thrill of the Fight 2 (Steam app 3279600).
DEFAULT_GAME_RECORDINGS = (
    Path.home()
    / ".local/share/Steam/steamapps/compatdata/3279600/pfx/drive_c/users/steamuser"
    / "AppData/LocalLow/Halfbrick Studios/The Thrill of the Fight 2/SessionRecordings"
)


def recordings_root() -> Path:
    return Path(os.environ.get("TOTF_RECORDINGS", "Recordings")).resolve()


def game_recordings_root() -> Path:
    raw = os.environ.get("TOTF_GAME_RECORDINGS")
    return Path(raw).expanduser() if raw else DEFAULT_GAME_RECORDINGS


class AnalyzeRequest(BaseModel):
    path: str


class ImportRequest(BaseModel):
    name: str


def _cache_key(path: Path) -> tuple[str, int, int]:
    stat = path.stat()
    return (str(path.resolve()), stat.st_mtime_ns, stat.st_size)


def resolve_zip(path_str: str, root: Path | None = None) -> Path:
    raw = Path(path_str)
    root = (root or recordings_root()).resolve()
    if raw.is_absolute():
        candidate = raw.resolve()
    else:
        candidate = (root / raw).resolve()
        if not candidate.is_file():
            candidate = raw.resolve()
    if candidate.suffix.lower() != ".zip" or not candidate.is_file():
        raise HTTPException(status_code=404, detail=f"session zip not found: {path_str}")
    return candidate


def analyze_path(path: Path, cache: dict | None = None) -> FightAnalytics:
    key = _cache_key(path)
    if cache is not None and key in cache:
        return cache[key]
    try:
        report = build_analytics(load_session(path))
    except (OSError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if cache is not None:
        cache[key] = report
    return report


def _created_utc(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    if "." in text:
        head, tail = text.split(".", 1)
        digits: list[str] = []
        suffix = ""
        for index, char in enumerate(tail):
            if char.isdigit() and not suffix:
                digits.append(char)
            else:
                suffix = tail[index:]
                break
        fraction = "".join(digits)[:6]
        text = f"{head}.{fraction}{suffix}" if fraction else f"{head}{suffix}"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc)
    millis = parsed.microsecond // 1000
    return parsed.strftime("%Y-%m-%dT%H:%M:%S") + f".{millis:03d}Z"


def bout_shorthand(rounds: object, round_seconds: object) -> str | None:
    """Boxing card shorthand: scheduled rounds by minutes, as in 3x2 or 12x3."""
    if isinstance(rounds, bool) or not isinstance(rounds, (int, float)):
        return None
    if isinstance(round_seconds, bool) or not isinstance(round_seconds, (int, float)):
        return None
    count = int(rounds)
    seconds = float(round_seconds)
    if count < 1 or seconds <= 0:
        return None
    if seconds % 60 == 0:
        return f"{count}x{int(seconds // 60)}"
    if seconds.is_integer():
        whole = int(seconds)
        return f"{count}x{whole // 60}:{whole % 60:02d}"
    return f"{count}x{seconds:g}s"


def _fighter_name(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    name = value.strip()
    return name or None


def _round_score(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)


def fight_card(meta: dict) -> dict[str, str | None]:
    """Fighter names and a BoxRec result from the local fighter's side.

    The badge is W-KO, L-KO, W-TKO, L-TKO, W-UD, L-UD, or D. A score win is UD.
    """
    card: dict[str, str | None] = {
        "red": None,
        "blue": None,
        "winner": None,
        "method": None,
        "local": None,
        "result": None,
    }
    for track in meta.get("m_tracks") or []:
        if not isinstance(track, dict):
            continue
        corner_id = track.get("m_corner")
        corner = next((name for name, number in COLOR_CORNER.items() if number == corner_id), None)
        if corner is None:
            continue
        card[corner] = _fighter_name(track.get("m_boxerName"))
        if track.get("m_isLocalPlayer") is True:
            card["local"] = corner
    scoring = meta.get("m_scoring") if isinstance(meta.get("m_scoring"), dict) else {}
    rounds = scoring.get("m_rounds") if isinstance(scoring.get("m_rounds"), list) else []
    if not rounds:
        return card
    try:
        completed = int(scoring.get("m_completedRoundIndex") or 0)
    except (TypeError, ValueError):
        completed = 0
    knocked: set[str] = set()
    technical: set[str] = set()
    scores = {"red": 0, "blue": 0}
    for index, raw in enumerate(rounds):
        if index > completed or not isinstance(raw, dict):
            continue
        for corner, key in (("red", "m_red"), ("blue", "m_blue")):
            block = raw.get(key) if isinstance(raw.get(key), dict) else {}
            if block.get("m_knockedOut"):
                knocked.add(corner)
            if block.get("m_technicallyKnockedOut"):
                technical.add(corner)
            scores[corner] += _round_score(block.get("m_score"))
    if len(knocked) == 1:
        card["method"] = "KO"
        card["winner"] = "blue" if "red" in knocked else "red"
    elif len(technical) == 1:
        card["method"] = "TKO"
        card["winner"] = "blue" if "red" in technical else "red"
    elif scores["red"] == scores["blue"]:
        card["method"] = "draw"
    else:
        card["method"] = "UD"
        card["winner"] = "red" if scores["red"] > scores["blue"] else "blue"
    card["result"] = _boxrec_result(card["winner"], card["method"], card["local"])
    return card


def _boxrec_result(winner: str | None, method: str | None, local: str | None) -> str | None:
    if method == "draw":
        return "D"
    if method not in {"KO", "TKO", "UD"} or winner not in {"red", "blue"}:
        return None
    subject = local if local in {"red", "blue"} else winner
    return f"{'W' if winner == subject else 'L'}-{method}"


def _bout_fields(path: Path) -> dict[str, str | None]:
    empty = {
        "bout": None,
        "created": None,
        "red": None,
        "blue": None,
        "winner": None,
        "method": None,
        "local": None,
        "result": None,
    }
    try:
        with zipfile.ZipFile(path) as archive:
            meta = json.loads(archive.read("metadata.json"))
    except (OSError, zipfile.BadZipFile, KeyError, json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return empty
    if not isinstance(meta, dict):
        return empty
    venue = meta.get("m_venue") if isinstance(meta.get("m_venue"), dict) else {}
    return {
        "bout": bout_shorthand(venue.get("m_rounds"), venue.get("m_roundDurationSeconds")),
        "created": _created_utc(meta.get("m_createdUtc")),
        **fight_card(meta),
    }


def _session_row(path: Path, imported: bool | None = None) -> dict:
    stat = path.stat()
    row = {"name": path.name, "size": stat.st_size, "modified": stat.st_mtime, **_bout_fields(path)}
    if imported is not None:
        row["imported"] = imported
    return row


def list_game_zips(game_root: Path, library: Path) -> dict:
    root = game_root.expanduser().resolve()
    imported = {path.name for path in library.glob("*.zip")} if library.is_dir() else set()
    rows: list[dict] = []
    if root.is_dir():
        paths = [path for path in root.glob("*.zip") if path.is_file()]
        for path in sorted(paths, key=lambda item: item.name, reverse=True):
            rows.append(_session_row(path, path.name in imported))
    return {"directory": str(root), "available": root.is_dir(), "recordings": rows}


def import_game_zip(name: str, game_root: Path, library: Path) -> Path:
    if Path(name).name != name or not name.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="name must be a session zip file name")
    source_root = game_root.expanduser().resolve()
    source = (source_root / name).resolve()
    if not source.is_relative_to(source_root):
        raise HTTPException(status_code=400, detail="recording is outside the game folder")
    if not source.is_file():
        raise HTTPException(status_code=404, detail=f"session zip not found: {name}")
    library.mkdir(parents=True, exist_ok=True)
    library = library.resolve()
    dest = (library / name).resolve()
    if not dest.is_relative_to(library):
        raise HTTPException(status_code=400, detail="invalid destination")
    try:
        shutil.copy2(source, dest)
    except shutil.SameFileError:
        pass
    return dest


def create_app(recordings: Path | None = None, game_recordings: Path | None = None) -> FastAPI:
    root = (recordings or recordings_root()).resolve()
    game_root = game_recordings or game_recordings_root()
    app = FastAPI(title="TotF Analyzer", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.recordings = root
    app.state.cache = {}

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "recordings": str(root)}

    @app.get("/api/sessions")
    def list_sessions() -> list[dict]:
        root.mkdir(parents=True, exist_ok=True)
        return [_session_row(path) for path in sorted(root.glob("*.zip")) if path.is_file()]

    @app.get("/api/game-recordings")
    def game_recordings() -> dict:
        return list_game_zips(game_root, root)

    @app.post("/api/sessions/import")
    def import_session(body: ImportRequest) -> dict:
        path = import_game_zip(body.name, game_root, root)
        return _session_row(path)

    @app.post("/api/sessions/analyze")
    def analyze(body: AnalyzeRequest) -> FightAnalytics:
        path = resolve_zip(body.path, root)
        return analyze_path(path, app.state.cache)

    @app.get("/api/sessions/{name}/report")
    def report(name: str) -> FightAnalytics:
        path = resolve_zip(name, root)
        return analyze_path(path, app.state.cache)

    @app.post("/api/sessions/upload")
    async def upload(file: UploadFile) -> FightAnalytics:
        if not file.filename or not file.filename.lower().endswith(".zip"):
            raise HTTPException(status_code=400, detail="upload a .zip session recording")
        data = await file.read()
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as handle:
            handle.write(data)
            temp_path = Path(handle.name)
        try:
            return analyze_path(temp_path, app.state.cache)
        finally:
            temp_path.unlink(missing_ok=True)

    dist = Path(__file__).resolve().parents[3] / "web" / "dist"
    index = dist / "index.html"
    assets = dist / "assets"
    if index.is_file() and assets.is_dir():
        from fastapi.staticfiles import StaticFiles

        app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/")
        def index_page() -> FileResponse:
            return FileResponse(index)

    return app


app = create_app()
