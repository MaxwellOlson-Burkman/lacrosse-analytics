# Pre-commit checklist

Run these before committing to avoid pushing raw HTML and keep the repo lean.

1. **Ignore raw data**  
   `.gitignore` includes `data/raw/` so scraped HTML is never committed.

2. **Stop tracking raw (if it was ever added)**  
   If you previously ran `git add .` and added `data/raw/`, run once:
   ```bash
   git rm -r --cached data/raw
   ```
   (If you get "pathspec 'data/raw' did not match any files", raw was never tracked; skip.)

3. **Stage and commit**  
   Use the commands in the section below.

4. **Verify**  
   After `git add .`, run `git status` and confirm `data/raw/` does not appear. Only `data/processed/`, `data/README.md`, code, config, and scripts should be staged.
