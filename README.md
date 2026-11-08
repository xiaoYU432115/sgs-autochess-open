# SGS Autochess Assistant

An unofficial desktop helper and browser extension for the autochess mode of
San Guo Sha (三国杀). The project contains a local Python application, a browser
bridge, and Chromium extension sources.

This repository is a source-only public snapshot. It intentionally excludes
personal configuration, account data, match logs, game-client files, extracted
season tables, screenshots, recordings, backups, and packaged executables.
Some features need local game data supplied by the user; those files are not
included here.

## Run locally

1. Install Python and the dependencies in `requirements.txt`.
2. Install Tesseract OCR and make it available on `PATH` if using OCR features.
3. Run `python main.py`.
4. For the browser bridge, load `bridge/sgs_ext/` as an unpacked extension in
   Chrome or Edge 120+, then open the game page with the local helper running.

The app creates `config.json`, `data/`, and other runtime state locally. Keep
those files private and do not commit them.

## Development

Run the focused scenario tests with:

```text
python -m unittest discover -s tests
```

The extension's bridge fallback is generated from `bridge/sgs_bridge.user.js`.
Use the scripts under `tools/` to build the extension module container.

## License and third-party rights

Original project code is provided under the MIT License; see `LICENSE`.
Third-party software, game content, trademarks, and any user-supplied game data
are not covered by that license. This project is unofficial and is not
affiliated with or endorsed by the game's publisher. Only use game data and
services in ways permitted by their respective terms.
