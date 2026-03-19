- Core functionality:
    - [ ] `ytget sync <target>`: run `check` + download missing + `repair` in one shot
    - [x] `ytget remove <target> <search>`: already planned, finish it out with `--delete-file`
    - [ ] `ytget migrate <old_target> <new_target>`: move a track between playlists in the manifest
    - [ ] `ytget stats`: total tracks, total size on disk, oldest/newest download, per-playlist breakdown

- Config integration:
    - [ ] Honor `default_output_dir` in `audio` command so `-o` is optional
    - [ ] `concurrent_downloads` wired into yt-dlp's `concurrent_fragment_downloads`
    - [ ] `quiet_mode` suppresses Rich progress bars for scripting/cron use

- UX polish:
    - [ ] `ytget list`: show all registered playlists with track counts and last sync time
    - [ ] Colorized diff in `check` showing exactly what changed since last sync (new additions vs removals)
    - [ ] `--dry-run` flag on `audio` to preview what would be downloaded without actually doing it

- Robustness:
    - [ ] Auto-repair during `audio`: if a manifest entry is `.m4a` before downloading, fix it inline rather than requiring a separate `repair` run
    - [ ] Retry logic with exponential backoff for rate-limited downloads
    - [ ] Lockfile (`~/.config/ytget/ytget.lock`) to prevent two simultaneous `ytget audio` runs on the same playlist

- Distribution:
    - [ ] `pyproject.toml` with proper entry point so `pip install .` just works
    - [ ] GitHub Actions CI: lint with `ruff`, run a dry-run smoke test
    - [ ] `ytget update`: self-update via `pip install --upgrade ytget`
