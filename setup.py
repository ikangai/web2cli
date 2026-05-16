"""
Build a standalone macOS .app bundle:

    python3 -m pip install --user py2app rumps
    python3 setup.py py2app

Output: dist/WebCLIBridge.app — drag into /Applications.
"""
from setuptools import setup

APP = ["bridge_app.py"]
OPTIONS = {
    "argv_emulation": False,
    "plist": {
        "CFBundleName": "WebCLIBridge",
        "CFBundleDisplayName": "Web CLI Bridge",
        "CFBundleIdentifier": "com.martintreiber.webclibridge",
        "CFBundleVersion": "0.2.0",
        "CFBundleShortVersionString": "0.2.0",
        "LSUIElement": True,
        "NSHumanReadableCopyright": "",
    },
    "packages": ["rumps"],
    "includes": ["server"],
}

setup(
    app=APP,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
