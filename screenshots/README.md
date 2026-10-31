# Screenshots

This folder is intentionally empty of images.

The project was built and executed in a headless Linux environment without a graphical
desktop, so genuine screenshots of VS Code or PowerShell could not be captured there.
No images were fabricated or mocked up.

After running the project on your own machine, save real screenshots here using these names
and then link them from the main `README.md`:

| File name | What to capture |
|-----------|-----------------|
| `01_pipeline_run.png` | PowerShell/VS Code terminal after `python main.py` (the Data Quality Report block) |
| `02_incremental_rerun.png` | Terminal after running `python main.py` a second time (`Loaded into warehouse : 0`) |
| `03_pytest_passed.png` | Terminal after `python -m pytest -v` showing `93 passed` |
| `04_warehouse_tables.png` | The four tables opened in a SQLite viewer (for example the *SQLite Viewer* VS Code extension) |
| `05_analytics_results.png` | Terminal after `python main.py --analytics` |
