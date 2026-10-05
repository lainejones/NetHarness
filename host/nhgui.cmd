@echo off
rem Remote-control window for NetHarness (see nhgui.py). Needs Python with Pillow.
start "" pythonw "%~dp0nhgui.py" %*
