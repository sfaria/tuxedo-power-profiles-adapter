#!/usr/bin/env python3

"""
DBus adapter enabling desktop environments to control TUXEDO power profiles via the freedesktop power-profiles dbus interface

TODO:
    - implement polkit
    - tccd support profile hold?
    - add full set of ppd dbus api
"""

import asyncio
import json

from dbus_next.aio import MessageBus
from dbus_next import BusType, Variant, DBusError, ErrorType
from dbus_next.service import ServiceInterface, dbus_property, PropertyAccess

import builtins
import functools
print = functools.partial(builtins.print, flush=True)

# ---- tccd DBus ----
TCCD_BUS = "com.tuxedocomputers.tccd"
TCCD_PATH = "/com/tuxedocomputers/tccd"
TCCD_INTERFACE = "com.tuxedocomputers.tccd"

# ---- PowerProfiles API ----s
BUS_NAME = "org.freedesktop.UPower.PowerProfiles"
OBJECT_PATH = "/"+BUS_NAME.replace(".", "/")
INTERFACE = BUS_NAME
    
# ---- Config ----
CONFIG_PATH = "/etc/tuxedo-power-profiles-adapter/config.toml"

# How often to re-read the active tccd profile, to notice changes made outside
# the adapter (TCC GUI, automatic AC/battery switching). tccd has no signal for this.
POLL_INTERVAL = 2

DEFAULT_MAP = {
    "power-saver": "__legacy_powersave_extreme__",
    "balanced": "__legacy_cool_and_breezy__",
    "performance": "__legacy_default__",
}

# load_config reads the config file and returns a mapping of PowerProfiles profile names to tccd profile IDs.
def load_config():
    import tomllib

    print(f"Loading config from {CONFIG_PATH}...",flush=True)

    PROFILE_MAP = dict(DEFAULT_MAP)

    try:
        with open(CONFIG_PATH, "rb") as f:
            config = tomllib.load(f)
    except FileNotFoundError:
        print(f"Warning: Config file {CONFIG_PATH} not found. Using default profile mapping.")
        return PROFILE_MAP

    profile_map = config.get("profile_map")
    if not isinstance(profile_map, dict):
        print(f"Warning: Invalid or missing 'profile_map' in config. Using default profile mapping.")
        return PROFILE_MAP

    allowed_profiles = set(DEFAULT_MAP)

    for name in allowed_profiles:
        tccd_id = profile_map.get(name)
        if isinstance(tccd_id, str) and tccd_id:
            PROFILE_MAP[name] = tccd_id

    unknown = set(profile_map.keys()) - allowed_profiles
    if unknown:
        print(f"Warning: Unknown profile_map keys ignored: {', '.join(sorted(unknown))}")

    return PROFILE_MAP

PROFILE_MAP = load_config()
REVERSE_MAP = {v: k for k, v in PROFILE_MAP.items()}


# TccdClient is responsible for communicating with tccd over D-Bus and providing 
# a simple API for getting the active profile and setting a new profile.
class TccdClient:

    def __init__(self, bus):
        self.bus = bus

    async def connect(self):

        introspection = await self.bus.introspect(TCCD_BUS, TCCD_PATH)

        obj = self.bus.get_proxy_object(
            TCCD_BUS,
            TCCD_PATH,
            introspection
        )

        self.iface = obj.get_interface(TCCD_INTERFACE)

    async def get_active(self):

        raw = await self.iface.call_get_active_profile_json()

        return json.loads(raw)

    async def set_profile(self, profile_id):

        return await self.iface.call_set_temp_profile_by_id(profile_id)


# PowerProfiles adapter implementation. 
# This class implements the PowerProfiles D-Bus API and translates calls to the tccd client.
class PowerProfiles(ServiceInterface):

    def __init__(self, tccd):

        super().__init__(INTERFACE)

        self.tccd = tccd

        self._active = "balanced"

        # Keeps polling from reading tccd while a profile switch is in progress.
        self._lock = asyncio.Lock()

        self._profiles = [
            {"Profile": Variant("s", "power-saver")},
            {"Profile": Variant("s", "balanced")},
            {"Profile": Variant("s", "performance")}
        ]

    async def init_state(self):

        active = await self.tccd.get_active()

        tccd_id = active["id"]

        if tccd_id in REVERSE_MAP:
            self._active = REVERSE_MAP[tccd_id]

    # poll_tccd follows profile changes made outside the adapter and announces them.
    # A tccd profile that isn't in the profile map leaves ActiveProfile unchanged.
    async def poll_tccd(self):

        while True:
            await asyncio.sleep(POLL_INTERVAL)

            try:
                async with self._lock:
                    active = await self.tccd.get_active()
            except Exception as e:
                print(f"Warning: Reading active tccd profile failed: {e}")
                continue

            profile = REVERSE_MAP.get(active.get("id"))

            if profile and profile != self._active:
                print(f"tccd profile changed outside the adapter: {active.get('id')} -> {profile}")
                self._set_active(profile)

    def _set_active(self, profile):

        self._active = profile

        self.emit_properties_changed(
            {"ActiveProfile": self._active},
            []
        )

    @dbus_property(access=PropertyAccess.READ)
    def Profiles(self) -> "aa{sv}":

        return self._profiles

    @dbus_property(access=PropertyAccess.READWRITE)
    def ActiveProfile(self) -> "s":

        return self._active

    # The setter is async so dbus-next waits for tccd before replying to the caller,
    # and sends any error back to them instead of losing it.
    @ActiveProfile.setter
    async def ActiveProfile(self, value: "s"):

        print(f"Set ActiveProfile -> {value}")

        await self._switch_profile(value)

    async def _switch_profile(self, profile):

        if profile not in PROFILE_MAP:
            raise DBusError(ErrorType.INVALID_ARGS, f"Unknown profile: {profile}")

        tccd_id = PROFILE_MAP[profile]

        async with self._lock:
            ok = await self.tccd.set_profile(tccd_id)

        print(f"tccd SetTempProfileById({tccd_id}) returned {ok}")

        # tccd returns true even for unknown IDs, so this only catches outright refusals.
        if not ok:
            raise DBusError(ErrorType.FAILED, f"tccd refused profile {tccd_id}")

        self._set_active(profile)

async def main():

    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

    tccd = TccdClient(bus)

    await tccd.connect()

    service = PowerProfiles(tccd)

    await service.init_state()

    bus.export(OBJECT_PATH, service)

    await bus.request_name(BUS_NAME)

    print("tccd power profiles adapter running")

    await service.poll_tccd()

if __name__ == "__main__":
    asyncio.run(main())