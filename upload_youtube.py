"""
upload_youtube.py — YouTube Upload Script for The Interested Indian

Uploads the finished MP4, thumbnail, and metadata to YouTube using the
Data API v3 with OAuth2. On first run it opens a browser for consent;
the token is cached in token.json (excluded from git) for subsequent runs.

Usage:
    python upload_youtube.py --project ep01
    python upload_youtube.py --project ep01 --schedule "2026-07-28T17:00:00+05:30"
    python upload_youtube.py --project ep01 --dry-run          # print what would happen

Reads (all from episode folder):
    episode_state.json     → title, description, tags
    metadata_*.txt         → VIRAL VIDEO TITLE / VIDEO DESCRIPTION / VIRAL VIDEO TAGS
    chapters.txt           → timestamp block (prepended to description)
    output/*_final.mp4     → the video file
    thumbnail.png          → the thumbnail

OAuth2:
    Credentials file: Youtube_Interested_Indian_Upload.json (same folder as this script)
    Token cache:      token.json (same folder, git-ignored)

Requirements:
    pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib --break-system-packages
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

PIPELINE_DIR = Path(__file__).parent
CREDENTIALS_FILE = PIPELINE_DIR / "Youtube_Interested_Indian_Upload.json"
TOKEN_FILE       = PIPELINE_DIR / "token.json"

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

# Default YouTube category for Education
CATEGORY_ID = "27"   # Education


# ── Auth ───────────────────────────────────────────────────────────────────────

def _get_authenticated_service():
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        print("❌ Google API libraries not found.")
        print("   Run: pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib --break-system-packages")
        sys.exit(1)

    if not CREDENTIALS_FILE.exists():
        print(f"❌ OAuth2 credentials file not found: {CREDENTIALS_FILE}")
        print("   Make sure Youtube_Interested_Indian_Upload.json is in the pipeline folder.")
        sys.exit(1)

    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)

        TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")

    return build("youtube", "v3", credentials=creds)


# ── Episode data readers ───────────────────────────────────────────────────────

def _find_video(project_dir: Path) -> Path | None:
    output_dir = project_dir / "output"
    if output_dir.exists():
        mp4s = sorted(output_dir.glob("*_final.mp4"))
        if mp4s:
            return mp4s[-1]
    return None


def _find_thumbnail(project_dir: Path) -> Path | None:
    p = project_dir / "thumbnail.png"
    return p if p.exists() else None


def _read_metadata(project_dir: Path) -> dict:
    """Read title, description, tags from metadata_*.txt and chapters.txt."""
    result = {"title": "", "description": "", "tags": []}

    # episode_state.json for title fallback
    state_path = project_dir / "episode_state.json"
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            result["title"] = state.get("data", {}).get("title", "")
        except Exception:
            pass

    # metadata_*.txt — primary source
    for meta_file in sorted(project_dir.glob("metadata_*.txt")):
        text = meta_file.read_text(encoding="utf-8")

        title_m = re.search(r"VIRAL VIDEO TITLE:\s*\n(.+)", text)
        if title_m:
            result["title"] = title_m.group(1).strip()

        desc_m = re.search(r"VIDEO DESCRIPTION:\s*\n(.*?)(?=\n[A-Z ]+:|\Z)", text, re.DOTALL)
        if desc_m:
            result["description"] = desc_m.group(1).strip()

        tags_m = re.search(r"VIRAL VIDEO TAGS:\s*\n(.+)", text, re.DOTALL)
        if tags_m:
            raw_tags = tags_m.group(1).strip().split("\n")[0]  # first line only
            result["tags"] = [t.strip() for t in raw_tags.split(",") if t.strip()]

        break   # use first metadata file found

    # chapters.txt — prepend to description
    chapters_path = project_dir / "chapters.txt"
    if chapters_path.exists():
        chapters_block = chapters_path.read_text(encoding="utf-8").strip()
        result["description"] = chapters_block + "\n\n" + result["description"]

    return result


# ── Upload ─────────────────────────────────────────────────────────────────────

def _upload_video(
    youtube,
    video_path: Path,
    title: str,
    description: str,
    tags: list[str],
    schedule: str | None = None,
) -> str:
    """Upload video and return the YouTube video ID."""
    try:
        from googleapiclient.http import MediaFileUpload
    except ImportError:
        print("❌ googleapiclient not found")
        sys.exit(1)

    status_obj = {"privacyStatus": "private"}
    if schedule:
        # schedule format: "2026-07-28T17:00:00+05:30"
        status_obj = {"privacyStatus": "private", "publishAt": schedule}
        print(f"  Scheduled publish: {schedule}")

    body = {
        "snippet": {
            "title":       title[:100],
            "description": description[:5000],
            "tags":        tags[:500],
            "categoryId":  CATEGORY_ID,
            "defaultLanguage": "en",
        },
        "status": status_obj,
    }

    media = MediaFileUpload(
        str(video_path),
        mimetype="video/mp4",
        resumable=True,
        chunksize=1024 * 1024 * 10,  # 10 MB chunks
    )

    print(f"  Uploading {video_path.name}  ({video_path.stat().st_size // (1024*1024)} MB)...")

    request    = youtube.videos().insert(part=",".join(body.keys()), body=body, media_body=media)
    response   = None
    last_pct   = -1

    while response is None:
        status, response = request.next_chunk()
        if status:
            pct = int(status.progress() * 100)
            if pct != last_pct:
                print(f"    {pct}%", end="\r", flush=True)
                last_pct = pct

    print()
    return response["id"]


def _upload_thumbnail(youtube, video_id: str, thumbnail_path: Path):
    try:
        from googleapiclient.http import MediaFileUpload
    except ImportError:
        return

    print(f"  Uploading thumbnail...")
    media = MediaFileUpload(str(thumbnail_path), mimetype="image/png")
    youtube.thumbnails().set(videoId=video_id, media_body=media).execute()
    print("  ✓ Thumbnail set")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project",  required=True, help="Episode folder (e.g. ep01)")
    parser.add_argument("--schedule", default=None,
                        help="ISO 8601 publish datetime (e.g. 2026-07-28T17:00:00+05:30). Omit to leave private.")
    parser.add_argument("--dry-run",  action="store_true", dest="dry_run",
                        help="Print what would be uploaded without actually uploading")
    args = parser.parse_args()

    project_dir = Path(args.project)
    if not project_dir.is_absolute():
        project_dir = PIPELINE_DIR / args.project
    if not project_dir.exists():
        print(f"❌ Project folder not found: {project_dir}")
        sys.exit(1)

    # ── Gather assets
    video_path     = _find_video(project_dir)
    thumbnail_path = _find_thumbnail(project_dir)
    meta           = _read_metadata(project_dir)

    print(f"\n  Project   : {project_dir.name}")
    print(f"  Title     : {meta['title']}")
    print(f"  Tags      : {len(meta['tags'])} tags")
    print(f"  Video     : {video_path.name if video_path else '❌ NOT FOUND'}")
    print(f"  Thumbnail : {thumbnail_path.name if thumbnail_path else '⚠ not found — YouTube default will be used'}")
    print(f"  Chapters  : {'✓ included in description' if (project_dir / 'chapters.txt').exists() else '✗ not found'}")
    if args.schedule:
        print(f"  Scheduled : {args.schedule}")
    else:
        print(f"  Status    : private (no schedule — set public manually in YouTube Studio)")

    if not video_path:
        print("\n❌ Cannot upload: no *_final.mp4 found in output/ folder.")
        sys.exit(1)

    if not meta["title"]:
        print("\n❌ No title found. Run generate metadata stage first.")
        sys.exit(1)

    if args.dry_run:
        print("\n  [dry-run] No upload performed.")
        if meta["description"]:
            print("\n── Description preview ──")
            print(meta["description"][:500] + ("..." if len(meta["description"]) > 500 else ""))
        return

    # ── Upload
    print("\n  Authenticating with YouTube...")
    youtube = _get_authenticated_service()

    video_id = _upload_video(
        youtube,
        video_path,
        title=meta["title"],
        description=meta["description"],
        tags=meta["tags"],
        schedule=args.schedule,
    )

    url = f"https://www.youtube.com/watch?v={video_id}"
    print(f"\n  ✓ Video uploaded: {url}")
    print(f"    ID: {video_id}")

    if thumbnail_path:
        _upload_thumbnail(youtube, video_id, thumbnail_path)

    # Save upload record
    record_path = project_dir / "upload_record.json"
    record = {
        "video_id":    video_id,
        "url":         url,
        "title":       meta["title"],
        "scheduled":   args.schedule,
        "video_file":  str(video_path),
        "thumbnail":   str(thumbnail_path) if thumbnail_path else None,
    }
    record_path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Record saved → {record_path.name}")

    print(f"\n  Next: go to YouTube Studio to add end screen, cards, and set public when ready.")
    print(f"  URL: {url}")


if __name__ == "__main__":
    main()
