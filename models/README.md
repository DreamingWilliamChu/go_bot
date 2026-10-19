# models/

Downloaded network files live here and are git-ignored. Only `manifest.json` is committed: it pins every
network's exact file name, source URL, size, sha256, license and kata1 Elo.

```bash
./scripts/download_models.sh            # default set: tf3-b10c512, tf2-b10c384, moku-v4 (b18 comes with Homebrew)
./scripts/download_models.sh --max      # + tf3-b11c768 (strongest, 262 MB)
./scripts/download_models.sh --all      # every network in the manifest
uv run gobot models list                # what is installed
```

Downloads are verified (size + sha256) before being moved into place. The Homebrew KataGo package also
installs `kata1-b18c384nbt-…` under `$(brew --prefix)/share/katago`, which the app finds automatically.
See docs/MODELS.md for how the default network is chosen.
