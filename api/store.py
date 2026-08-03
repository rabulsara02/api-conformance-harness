"""
In-memory data store.

Deliberately NOT a database. This service exists to be tested, not to persist
anything. A dict keeps the whole system inspectable, makes every test start from
an identical known state, and avoids dragging in migrations, connection pools
and teardown -- an entire category of failure that has nothing to do with
contract testing.

State lives at module level, so it survives across requests within one running
process and resets whenever that process restarts.
"""

from api.models import Device, DeviceStatus

# Device id -> Device. Module-level, so all requests share it.
_DEVICES: dict[int, Device] = {}

# The id the next created device will receive.
_next_id: int = 1


def reset() -> None:
    """
    Restore the store to its seeded starting state.

    Tests call this before each case so every test begins from identical data.
    Without it, a test that creates or deletes a device would silently change
    the outcome of tests running after it -- test-order dependence, one of the
    classic causes of flaky tests (see LEARNING_NOTES.md, Day 0). Resetting
    eliminates that whole class of problem by construction rather than by
    careful ordering.

    The id counter is reset too. Forgetting that would make ids depend on how
    many tests ran earlier, which is the same order-dependence bug wearing a
    different hat.
    """
    global _next_id

    _DEVICES.clear()
    seed = (
        Device(id=1, name="edge-router-01", status=DeviceStatus.ONLINE),
        Device(id=2, name="edge-router-02", status=DeviceStatus.OFFLINE),
        Device(id=3, name="sensor-gateway-01", status=DeviceStatus.DEGRADED),
    )
    for device in seed:
        _DEVICES[device.id] = device
    _next_id = max(_DEVICES) + 1


def list_devices() -> list[Device]:
    """
    Return every device, ordered by id.

    The explicit sort is a deliberate anti-flakiness measure. A dict preserves
    insertion order, so results would *probably* be consistent -- and "probably
    consistent" is precisely the property that produces a test which fails once
    a month for no visible reason. Deterministic ordering makes the response
    reproducible by construction.
    """
    return [_DEVICES[device_id] for device_id in sorted(_DEVICES)]


def get_device(device_id: int) -> Device | None:
    """Return the device with this id, or None if there isn't one."""
    return _DEVICES.get(device_id)


def find_by_name(name: str) -> Device | None:
    """
    Return the device with this exact name, or None.

    Used to enforce name uniqueness, which is what makes a duplicate create a
    409 Conflict rather than a silent second device.
    """
    for device in _DEVICES.values():
        if device.name == name:
            return device
    return None


def create_device(name: str, status: DeviceStatus) -> Device:
    """
    Add a new device and return it, with a server-assigned id.

    Ids are handed out by a counter that only ever increases -- they are NOT
    reused after a delete. Reuse would mean a client holding id 4 could later
    find a completely different device there, with no way to tell. Monotonic ids
    make a stale reference produce an honest 404 instead of silently wrong data.
    """
    global _next_id

    device = Device(id=_next_id, name=name, status=status)
    _DEVICES[device.id] = device
    _next_id += 1
    return device


def replace_device(device_id: int, name: str, status: DeviceStatus) -> Device | None:
    """
    Overwrite an existing device wholesale (PUT semantics). None if absent.

    Builds a brand-new Device rather than mutating the stored one, so the value
    goes through Pydantic validation again. Mutating in place would let invalid
    data in through the side door.
    """
    if device_id not in _DEVICES:
        return None

    device = Device(id=device_id, name=name, status=status)
    _DEVICES[device_id] = device
    return device


def delete_device(device_id: int) -> bool:
    """
    Remove a device. Returns True if it existed, False otherwise.

    Returning a bool rather than raising lets the route layer decide what the
    absence of a device means in HTTP terms. The store deals in data; only the
    route deals in status codes. Keeping that boundary clean is what will make
    the seeded bug modes on Day 6 easy to add in one place.
    """
    return _DEVICES.pop(device_id, None) is not None


def search_devices(
    name_contains: str | None = None,
    status: DeviceStatus | None = None,
) -> list[Device]:
    """
    Return devices matching the given filters, ordered by id.

    Both filters are optional and combine with AND. Passing neither returns
    everything -- the same result as list_devices(), which is the least
    surprising behaviour for an empty search.

    Name matching is case-insensitive substring, chosen because it gives the
    Day 10 property-based fuzzer something interesting to attack (empty strings,
    unicode, very long inputs) without needing a real search engine.
    """
    results = list_devices()

    if name_contains is not None:
        needle = name_contains.lower()
        results = [d for d in results if needle in d.name.lower()]

    if status is not None:
        results = [d for d in results if d.status == status]

    return results


# Populate on import, so the app has data as soon as it starts.
reset()