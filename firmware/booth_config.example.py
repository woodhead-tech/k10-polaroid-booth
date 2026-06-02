# Photo booth venue config — copy to booth_config.py and fill in before each event.
# booth_config.py is gitignored (it holds the live WiFi password); this example is
# the tracked template.
#
# Per-device: set CAM_ID to cam1 … camN (one per K10 unit).
# SERVER_URL: the LAN IP:port of the machine running the booth server
#             (graduation-site/booth/server.py, default port 8080).

SSID       = "YourHotspot"
PASSWORD   = "YourPassword"
SERVER_URL = "http://SERVER_IP:8080"
CAM_ID     = "cam1"
EVENT      = "Ender's Graduation - June 2026"
