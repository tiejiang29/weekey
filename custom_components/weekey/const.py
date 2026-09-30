"""Constants for the Weekey integration."""

from __future__ import annotations

DOMAIN = "weekey"

CONF_PHPSESSID = "phpsessid"
CONF_GATE_IDS = "gate_ids"
CONF_UPDATE_INTERVAL = "update_interval"

DEFAULT_UPDATE_INTERVAL_MIN = 15
MIN_UPDATE_INTERVAL_MIN = 5

# Unlock cooldown: guards against double-tap and against repeatedly driving
# electric-strike hardware that the vendor itself asks users to confirm before opening.
UNLOCK_COOLDOWN = 10

# How long the switch stays "on" after a release command is accepted. The switch
# state is synthetic (the vendor reports no lock state), so this is purely a UI
# fall-back edge; it must stay well under UNLOCK_COOLDOWN.
SWITCH_RESET_SECONDS = 3

# Route names from the vendor SPA: 1/2 = 乘梯, 4 = 呼梯. Those reuse the unlock
# endpoint with a different `floor` meaning, so they are never exposed as door switches.
ELEVATOR_TYPES = frozenset({"1", "2", "4"})

BASE_URL = "https://s.weekey.cn"
PATH_INDEX = "/mobile_v2/index"
PATH_ONEKEY = "/SmallAPP/Unlock/onekey"
PATH_UNLOCK_NEW = "/SmallAPP/Unlock/unlock_new"

# Verified working against the endpoints during reverse engineering. A custom
# client UA has NOT been tested; change this in one place if the vendor starts
# gating it.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36"
)

REQUEST_TIMEOUT = 15
