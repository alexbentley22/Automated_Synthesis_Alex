from unitpy import Unit, Quantity

"""
Syringe specification database for Chembot.

Purpose
-------
- Provide a centralized, unit-safe catalog of syringe geometries and limits.
- Standardize syringe metadata used by syringe pumps for:
    * diameter (volume ↔ linear displacement conversion),
    * pressure / temperature safety limits,
    * force requirements,
    * vendor identification.
- Enable downstream code to configure pumps correctly by syringe model name.

Notes
-----
- All dimensional values are expressed using `unitpy` (Quantity).
- Keys are human-readable syringe identifiers used throughout the system.
- This file contains no logic—only reference data.
"""

syringe_configs = {
    # --------------------------------------------------------------
    # Hamilton glass syringes (1000-series)
    # --------------------------------------------------------------

    "hamilton_1001": {
        "volume": 1 * Unit.ml,                 # nominal syringe volume
        "max_pressure": 13.8 * Unit.bar,       # pressure rating
        "min_pressure": 0 * Unit.bar,
        "max_temperature": 115 * Unit.degC,
        "min_temperature": 10 * Unit.degC,
        "diameter": 4.61 * Unit.mm,            # inner diameter (critical!)
        "vendor": "hamilton",
        "force": 50                            # nominal plunger force rating
    },

    "hamilton_1002": {
        "volume": 2.5 * Unit.ml,
        "max_pressure": 13.8 * Unit.bar,
        "min_pressure": 0 * Unit.bar,
        "max_temperature": 115 * Unit.degC,
        "min_temperature": 10 * Unit.degC,
        "diameter": 7.29 * Unit.mm,
        "vendor": "hamilton",
        "force": 50
    },

    "hamilton_1005": {
        "volume": 5 * Unit.ml,
        "max_pressure": 13.8 * Unit.bar,
        "min_pressure": 0 * Unit.bar,
        "max_temperature": 115 * Unit.degC,
        "min_temperature": 10 * Unit.degC,
        "diameter": 10.3 * Unit.mm,
        "vendor": "hamilton",
        "force": 50
    },

    "hamilton_1010": {
        "volume": 10 * Unit.ml,
        "max_pressure": 13.8 * Unit.bar,
        "min_pressure": 0 * Unit.bar,
        "max_temperature": 115 * Unit.degC,
        "min_temperature": 10 * Unit.degC,
        "diameter": 14.567 * Unit.mm,
        "vendor": "hamilton",
        "force": 50
    },

    "hamilton_1025": {
        "volume": 25 * Unit.ml,
        "max_pressure": 6.9 * Unit.bar,
        "min_pressure": 0 * Unit.bar,
        "max_temperature": 85 * Unit.degC,
        "min_temperature": 10 * Unit.degC,
        "diameter": 23.0 * Unit.mm,
        "vendor": "hamilton",
        "force": 50
    },

    # --------------------------------------------------------------
    # KD Scientific stainless steel syringe
    # --------------------------------------------------------------

    "KDS_SS_780802": {
        # Stainless steel syringe
        # https://www.kdscientific.com/kds-stainless-steel-syringes.html
        "volume": 8 * Unit.ml,
        "max_pressure": 1500 * Unit.psi,        # much higher pressure rating
        "min_pressure": 0 * Unit.bar,
        "max_temperature": 85 * Unit.degC,
        "min_temperature": 10 * Unit.degC,
        "diameter": 9.525 * Unit.mm,
        "vendor": "kd_scientific",
        "force": 100                            # higher force due to steel plunger
    },

    # --------------------------------------------------------------
    # Norm-Ject disposable plastic syringes
    # --------------------------------------------------------------

    "norm_ject_5ml": {
        # https://www.restek.com/p/22768
        "volume": 5 * Unit.ml,
        "max_pressure": 10 * Unit.psi,
        "min_pressure": 0 * Unit.bar,
        "max_temperature": 30 * Unit.degC,
        "min_temperature": 20 * Unit.degC,
        "diameter": 12.2 * Unit.mm,             # manufacturer nominal diameter
        "vendor": "norm_ject",
        "force": 30
    },

    "norm_ject_2ml": {
        "volume": 2 * Unit.ml,
        "max_pressure": 10 * Unit.psi,
        "min_pressure": 0 * Unit.bar,
        "max_temperature": 30 * Unit.degC,
        "min_temperature": 20 * Unit.degC,
        "diameter": 9.65 * Unit.mm,
        "vendor": "norm_ject",
        "force": 30
    },

    "norm_ject_20ml": {
        "volume": 20 * Unit.ml,
        "max_pressure": 10 * Unit.psi,
        "min_pressure": 0 * Unit.bar,
        "max_temperature": 30 * Unit.degC,
        "min_temperature": 20 * Unit.degC,
        "diameter": 20.05 * Unit.mm,
        "vendor": "norm_ject",
        "force": 30
    },

    "norm_ject_10ml": {
        "volume": 10 * Unit.ml,
        "max_pressure": 10 * Unit.psi,
        "min_pressure": 0 * Unit.bar,
        "max_temperature": 30 * Unit.degC,
        "min_temperature": 20 * Unit.degC,
        "diameter": 15.90 * Unit.mm,
        "vendor": "norm_ject",
        "force": 30
    }
}
