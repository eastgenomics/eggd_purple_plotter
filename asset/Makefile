SHELL=/bin/bash -e -x -o pipefail

# Everything is installed under /opt (plus the apt libraries Chromium needs), so the
# asset bundle is whatever this target adds to a clean Ubuntu 24.04 image.
VENV=/opt/pp_render
BROWSERS=/opt/ms-playwright
PINS=playwright==1.64.1 greenlet==3.5.6 pyee==14.0.0 typing_extensions==4.16.0

all:
	apt-get update -y
	DEBIAN_FRONTEND=noninteractive apt-get install -y python3-venv
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install --no-deps $(PINS)
	PLAYWRIGHT_BROWSERS_PATH=$(BROWSERS) $(VENV)/bin/playwright install-deps chromium-headless-shell
	PLAYWRIGHT_BROWSERS_PATH=$(BROWSERS) $(VENV)/bin/playwright install --only-shell chromium
	chmod -R a+rX $(VENV) $(BROWSERS)
