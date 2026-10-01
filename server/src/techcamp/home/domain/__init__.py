"""Home domain package — intentionally rule-free.

`home` composes what `farms`, `telemetry`, `weather`, `irrigation` and `alerts`
already decided (D-T0.1): the crop stage, the representative sensor, the
water-balance status, the `null` rules and the open-alert order are each
module's own rule and are reused, never reimplemented here. A rule that belongs
to no other module would land in this package; there is none in E9 T2.
"""
