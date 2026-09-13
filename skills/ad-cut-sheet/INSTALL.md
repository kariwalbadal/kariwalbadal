# Install

Personal skill — available in every project on your machine.

```bash
cd ~/path/to/kariwalbadal && git pull
cp -r skills/ad-cut-sheet ~/.claude/skills/
pip3 install scenedetect opencv-python-headless librosa soundfile jsonschema pytesseract
brew install ffmpeg tesseract   # or: apt-get install -y ffmpeg tesseract-ocr
```

Verify: start a new Claude Code session anywhere and ask for a cut sheet of any video.
The skill triggers on "analyse/decompose/tear down/clone this ad", "cut sheet", "shot
list", "EDL", "edit rhythm", or "find the usable seconds in these clips".

Run directly without an agent:

```bash
python3 ~/.claude/skills/ad-cut-sheet/scripts/cut_sheet.py REF.mp4 --out out/ --product "the X"
python3 ~/.claude/skills/ad-cut-sheet/scripts/harvest_cli.py clip*.mp4 --product-ref p.png
```
