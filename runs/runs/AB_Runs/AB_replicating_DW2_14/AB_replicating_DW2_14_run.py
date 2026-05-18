from datetime import timedelta
from typing import Iterable

from unitpy import Unit, Quantity
import numpy as np

from chembot.scheduler import JobSequence, JobConcurrent, Event
from chembot.scheduler.job_submitter import JobSubmitter

from chembot.equipment.valves import ValveServo
from chembot.equipment.pumps import SyringePumpHarvard
from chembot.equipment.sensors import ATIR, NMR
from chembot.equipment.lights import LightPico
from chembot.equipment.temperature_control import PolyRecirculatingBath

from chembot.equipment.continuous_event_handler import ContinuousEventHandlerRepeatingNoEndSaving, \
    ContinuousEventHandlerProfile

from runs.launch_equipment.names import NamesPump, NamesValves, NamesSensors, NamesLEDColors, NamesEquipment

# Define DynamicProfile class. It's properties correspond to the columns of the dynamics profile 
# (currently includes time, bath temp, light intensity, pump 1 flowrate, pump 2 flowrate, pump 3 flowrate)
class DynamicProfile:
    def __init__(self, profile: np.ndarray):
        self.profile = profile

    @property
    def time(self) -> np.ndarray:
        return self.profile[:, 0] * 60  # min to sec conversion

    @property
    def temp(self) -> np.ndarray:
        return self.profile[:, 1]

    @property
    def light(self):
        return self.profile[:, 2]

    @property
    def Q1(self):
        return self.profile[:, 3]

    @property
    def Q2(self):
        return self.profile[:, 4]

    @property
    def Qfl(self):
        return self.profile[:, 5]


    @staticmethod
    def get_volume(t: np.ndarray, flow_rate: np.ndarray):
        return np.trapz(x=t, y=flow_rate)


# Load in dynamic profile data. Change filename to match what you have
def load_profiles() -> DynamicProfile:
    # t, temp, light, Q1, Q2, Qfl
    data = np.loadtxt(r"AB_replicating_DW2_14_profiles.csv", delimiter=",")
    return DynamicProfile(data)

# Defines a job for flow in the system. Takes total flow volume, pump flowrate, valve name, pump name, and duration of flow as inputs.
# If no duration is given, it will just run for 100 ms
def job_flow(volume: Quantity, flow_rate: Quantity, valve: str, pump: str, duration: bool = True) -> JobSequence:
    job = JobSequence(
        [
            Event(
                resource=valve,
                callable_=ValveServo.write_move,
                duration=timedelta(seconds=1.5),
                kwargs={"position": "flow"}
            ),
            Event(
                resource=pump,
                callable_=SyringePumpHarvard.write_infuse,
                duration=SyringePumpHarvard.compute_run_time(volume, flow_rate).to_timedelta(),
                kwargs={"volume": volume, "flow_rate": flow_rate}
            ),
            Event(  # here to stop alarm on pump if it is sounded
                resource=pump,
                callable_=SyringePumpHarvard.write_stop,
                duration=timedelta(milliseconds=1),
            )
        ]
    )
    if not duration:
        job = JobSequence(
            [
                Event(
                    resource=valve,
                    callable_=ValveServo.write_move,
                    duration=timedelta(seconds=1.5),
                    kwargs={"position": "flow"}
                ),
                Event(
                    resource=pump,
                    callable_=SyringePumpHarvard.write_infuse,
                    duration=timedelta(milliseconds=100),
                    kwargs={"volume": volume, "flow_rate": flow_rate}
                )
            ]
        )

    return job

# Defines job for filling syringe. Takes total fill volume, flowrate, valve name, and pump name as inputs.
# Fills with slightly extra volume and then pumps back into vial
def job_fill_syringe(volume: Quantity, flow_rate: Quantity, valve: str, pump: str) -> JobSequence:
    extra_volume = 0.5 * Unit.ml
    volume = volume + extra_volume
    return JobSequence(
        [
            Event(
                resource=valve,
                callable_=ValveServo.write_move,
                duration=timedelta(seconds=1.5),
                kwargs={"position": "fill"}
            ),
            Event(
                resource=pump,
                callable_=SyringePumpHarvard.write_withdraw,
                duration=SyringePumpHarvard.compute_run_time(volume, flow_rate).to_timedelta(),
                kwargs={"volume": volume, "flow_rate": flow_rate}
            ),
            Event(  # here to stop alarm on pump if it is sounded
                resource=pump,
                callable_=SyringePumpHarvard.write_stop,
                duration=timedelta(seconds=1),
            ),
            Event(
                resource=pump,
                callable_=SyringePumpHarvard.write_infuse,
                duration=SyringePumpHarvard.compute_run_time(extra_volume, flow_rate * 0.5).to_timedelta(),
                kwargs={"volume": extra_volume, "flow_rate": flow_rate * 0.5}
            ),
            Event(  # here to stop alarm on pump if it is sounded
                resource=pump,
                callable_=SyringePumpHarvard.write_stop,
                duration=timedelta(milliseconds=1),
            )
        ]
    )

# Same thing as above, but for multiple pumps
def job_fill_syringe_multiple(
        volume: Iterable[Quantity],
        flow_rate: Iterable[Quantity],
        valves: Iterable[str],
        pumps: Iterable[str],
) -> JobConcurrent:
    return JobConcurrent(
        [job_fill_syringe(vol, flow, val, p) for vol, flow, val, p in zip(volume, flow_rate, valves, pumps)],
    )

# Same thing as above, but for multiple pumps
def job_flow_syringe_multiple(
        volume: Iterable[Quantity],
        flow_rate: Iterable[Quantity],
        valves: Iterable[str],
        pumps: Iterable[str],
        delay: timedelta = None,
        duration: bool = True
) -> JobConcurrent:
    return JobConcurrent(
        [job_flow(vol, flow, val, p, duration) for vol, flow, val, p in zip(volume, flow_rate, valves, pumps)],
        delay=delay  # to allow syringes to stabilize
    )

# Defines a job that will run the system at a steady state. The steady state is at the first point of the dynamic profile.
# Do this at the start and end of every run to flush anything in the tubes as well as establish a baseline NMR/IR 
def steady_state(profile: DynamicProfile):
    Q1 = float(profile.Q1[0]) * Unit("ml") * 7 * 6  # (3*7min) * flowrate(ml/min) = ml|7min residence time
    Q2 = float(profile.Q2[0]) * Unit("ml") * 7 * 6
    Qfl = float(profile.Qfl[0]) * Unit("ml") * 7 * 6

    return JobSequence(
        [
            Event(
                resource=NamesEquipment.BATH,
                callable_=PolyRecirculatingBath.write_set_point,
                duration=timedelta(milliseconds=100),
                kwargs={"temperature": float(profile.temp[0])},
            ),

            job_fill_syringe_multiple(
                volume=[
                    Q1,
                    Q2,
                    Qfl,
                ],
                flow_rate=[
                    1 * Unit("ml/min"),
                    1 * Unit("ml/min"),
                    0.8 * Unit("ml/min"),
                ],
                valves=[NamesValves.ONE, NamesValves.TWO, NamesValves.THREE],
                pumps=[NamesPump.ONE, NamesPump.TWO, NamesPump.THREE]
            ),


            Event(
                resource=NamesLEDColors.GREEN,
                callable_=LightPico.write_power,
                duration=timedelta(milliseconds=100),
                kwargs={"power": int(profile.light[0] * 65535)},
            ),
            job_flow_syringe_multiple(
                volume=[
                    Q1,
                    Q2,
                    Qfl,
                ],
                flow_rate=[
                    float(profile.Q1[0]) * Unit("ml/min"),
                    float(profile.Q2[0]) * Unit("ml/min"),
                    float(profile.Qfl[0]) * Unit("ml/min"),
                ],
                valves=[NamesValves.ONE, NamesValves.TWO, NamesValves.THREE],
                pumps=[NamesPump.ONE, NamesPump.TWO, NamesPump.THREE]
            ),

            # stop
            Event(
                resource=NamesLEDColors.GREEN,
                callable_=LightPico.write_stop,
                duration=timedelta(milliseconds=100),
            ),
        ]
    )


def job_segment(profile: DynamicProfile, start: int, end: int):
    t = profile.time[start: end]
    t = t - t[0]
    temp = profile.temp[start: end]
    light = profile.light[start: end]
    Q1 = profile.Q1[start: end]
    Q2 = profile.Q2[start: end]
    Qfl = Q1 + Q2 

    Q1_vol = profile.get_volume(t/60, Q1) * Unit("ml")
    Q2_vol = profile.get_volume(t/60, Q2) * Unit("ml")
    fl_vol = profile.get_volume(t/60, Qfl) * Unit("ml")

    # power is [0, 65535]
    light_profile = ContinuousEventHandlerProfile(LightPico.write_power, ["power"], (light * 65535).astype(np.int32), t)
    temp_profile = ContinuousEventHandlerProfile(PolyRecirculatingBath.write_set_point, ["temperature"], temp, t)
    _Q1 = tuple(f * Unit("ml/min") for f in Q1)
    Q1_profile = ContinuousEventHandlerProfile(
        SyringePumpHarvard.write_infusion_rate, ["flow_rate"], _Q1, t)
    _Q2 = tuple(f * Unit("ml/min") for f in Q2)
    Q2_profile = ContinuousEventHandlerProfile(
        SyringePumpHarvard.write_infusion_rate, ["flow_rate"], _Q2, t)
    _Qfl = tuple(f * Unit("ml/min") for f in Qfl)
    fl_profile = ContinuousEventHandlerProfile(
        SyringePumpHarvard.write_infusion_rate, ["flow_rate"], _Qfl, t)

    return JobSequence(
        [
            job_fill_syringe_multiple(
                volume=[
                    Q1_vol,
                    Q2_vol,
                    fl_vol,
                ],
                flow_rate=[
                    1 * Unit("ml/min"),
                    1 * Unit("mL/min"),
                    0.8 * Unit("ml/min"),
                ],
                valves=[NamesValves.ONE, NamesValves.TWO, NamesValves.THREE],
                pumps=[NamesPump.ONE, NamesPump.TWO, NamesPump.THREE]
            ),

            job_flow_syringe_multiple(
                volume=[
                    Q1_vol * 2,
                    Q2_vol * 2,
                    fl_vol * 2,
                ],
                flow_rate=[
                    float(Q1[0]) * Unit("ml/min"),
                    float(Q2[0]) * Unit("ml/min"),
                    float(Qfl[0]) * Unit("ml/min"),
                ],
                valves=[NamesValves.ONE, NamesValves.TWO, NamesValves.THREE],
                pumps=[NamesPump.ONE, NamesPump.TWO, NamesPump.THREE],
                duration=False  # <<<<< will not block
            ),

            JobConcurrent(
                [
                    Event(
                        resource=NamesLEDColors.GREEN,
                        callable_=LightPico.write_continuous_event_handler,
                        duration=timedelta(milliseconds=100),
                        kwargs={"event_handler": light_profile},
                    ),
                    Event(
                        resource=NamesEquipment.BATH,
                        callable_=PolyRecirculatingBath.write_continuous_event_handler,
                        duration=timedelta(milliseconds=100),
                        kwargs={"event_handler": temp_profile},
                    ),
                    Event(
                        resource=NamesPump.ONE,
                        callable_=SyringePumpHarvard.write_continuous_event_handler,
                        duration=timedelta(milliseconds=100),
                        kwargs={"event_handler": Q1_profile},
                    ),
                    Event(
                        resource=NamesPump.TWO,
                        callable_=SyringePumpHarvard.write_continuous_event_handler,
                        duration=timedelta(milliseconds=100),
                        kwargs={"event_handler": Q2_profile},
                    ),
                    Event(
                        resource=NamesPump.THREE,
                        callable_=SyringePumpHarvard.write_continuous_event_handler,
                        duration=timedelta(milliseconds=100),
                        kwargs={"event_handler": fl_profile},
                    ),
                ],
                delay=timedelta(seconds=1)
            ),

            # stop
            JobConcurrent(
                [
                    Event(
                        resource=NamesLEDColors.GREEN,
                        callable_=LightPico.write_stop,
                        duration=timedelta(milliseconds=100),
                    ),
                    Event(
                        resource=NamesEquipment.BATH,
                        callable_=PolyRecirculatingBath.write_set_point,
                        duration=timedelta(milliseconds=100),
                        kwargs={"temperature": float(temp[-1])},
                    ),
                    Event(
                        resource=NamesPump.ONE,
                        callable_=LightPico.write_stop,
                        duration=timedelta(milliseconds=100),
                    ),
                    Event(
                        resource=NamesPump.TWO,
                        callable_=LightPico.write_stop,
                        duration=timedelta(milliseconds=100),
                    ),
                    Event(
                        resource=NamesPump.THREE,
                        callable_=LightPico.write_stop,
                        duration=timedelta(milliseconds=100),
                    ),
                ],
                delay=timedelta(seconds=t[-1])
            )
        ]
    )


def job_segment_start(profile: DynamicProfile, start: int, end: int):
    t = profile.time[start: end]
    t = t - t[0]
    temp = profile.temp[start: end]
    light = profile.light[start: end]
    Q1 = profile.Q1[start: end]
    Q2 = profile.Q2[start: end]
    Qfl = Q1 + Q2 

    Q1_vol = profile.get_volume(t/60, Q1) * Unit("ml")
    Q2_vol = profile.get_volume(t/60, Q2) * Unit("ml")
    fl_vol = profile.get_volume(t/60, Qfl) * Unit("ml")


    # power is [0, 65535]
    light_profile = ContinuousEventHandlerProfile(LightPico.write_power, ["power"], (light * 65535).astype(np.int32), t)
    temp_profile = ContinuousEventHandlerProfile(PolyRecirculatingBath.write_set_point, ["temperature"], temp, t)
    _Q1 = tuple(f * Unit("ml/min") for f in Q1)
    Q1_profile = ContinuousEventHandlerProfile(
        SyringePumpHarvard.write_infusion_rate, ["flow_rate"], _Q1, t)
    _Q2 = tuple(f * Unit("ml/min") for f in Q2)
    Q2_profile = ContinuousEventHandlerProfile(
        SyringePumpHarvard.write_infusion_rate, ["flow_rate"], _Q2, t)
    _Qfl = tuple(f * Unit("ml/min") for f in Qfl)
    fl_profile = ContinuousEventHandlerProfile(
        SyringePumpHarvard.write_infusion_rate, ["flow_rate"], _Qfl, t)

    return JobSequence(
        [
            job_flow_syringe_multiple(
                volume=[
                    Q1_vol * 2,
                    Q2_vol * 2,
                    fl_vol * 2,
                ],
                flow_rate=[
                    float(Q1[0]) * Unit("ml/min"),
                    float(Q2[0]) * Unit("ml/min"),
                    float(Qfl[0]) * Unit("ml/min"),
                ],
                valves=[NamesValves.ONE, NamesValves.TWO, NamesValves.THREE],
                pumps=[NamesPump.ONE, NamesPump.TWO, NamesPump.THREE],
                duration=False  # <<<<< will not block
            ),

            JobConcurrent(
                [
                    Event(
                        resource=NamesLEDColors.GREEN,
                        callable_=LightPico.write_continuous_event_handler,
                        duration=timedelta(milliseconds=100),
                        kwargs={"event_handler": light_profile},
                    ),
                    Event(
                        resource=NamesEquipment.BATH,
                        callable_=PolyRecirculatingBath.write_continuous_event_handler,
                        duration=timedelta(milliseconds=100),
                        kwargs={"event_handler": temp_profile},
                    ),
                    Event(
                        resource=NamesPump.ONE,
                        callable_=SyringePumpHarvard.write_continuous_event_handler,
                        duration=timedelta(milliseconds=100),
                        kwargs={"event_handler": Q1_profile},
                    ),
                    Event(
                        resource=NamesPump.TWO,
                        callable_=SyringePumpHarvard.write_continuous_event_handler,
                        duration=timedelta(milliseconds=100),
                        kwargs={"event_handler": Q2_profile},
                    ),
                    Event(
                        resource=NamesPump.THREE,
                        callable_=SyringePumpHarvard.write_continuous_event_handler,
                        duration=timedelta(milliseconds=100),
                        kwargs={"event_handler": fl_profile},
                    ),
                ],
                delay=timedelta(seconds=1)
            ),

            # stop
            JobConcurrent(
                [
                    Event(
                        resource=NamesLEDColors.GREEN,
                        callable_=LightPico.write_stop,
                        duration=timedelta(milliseconds=100),
                    ),
                    Event(
                        resource=NamesEquipment.BATH,
                        callable_=PolyRecirculatingBath.write_set_point,
                        duration=timedelta(milliseconds=100),
                        kwargs={"temperature": float(temp[-1])},
                    ),
                    Event(
                        resource=NamesPump.ONE,
                        callable_=LightPico.write_stop,
                        duration=timedelta(milliseconds=100),
                    ),
                    Event(
                        resource=NamesPump.TWO,
                        callable_=LightPico.write_stop,
                        duration=timedelta(milliseconds=100),
                    ),
                    Event(
                        resource=NamesPump.THREE,
                        callable_=LightPico.write_stop,
                        duration=timedelta(milliseconds=100),
                    ),
                ],
                delay=timedelta(seconds=t[-1])
            )
        ]
    )

# Overall job. Loads in the dynamic profile. You need to input refill indices that you got from dynamic calc.
# It turns on ATIR and NMR, runs steady state, runs job segments, then runs steady state before turning everything off.
# You will need to add/remove job segment lines based on the number of refill indices
def job_droplets() -> JobSequence:
    profiles = load_profiles()
    index_refill = [675, 1349, 2022]  # [112.54224559 224.91776192 337.126549]

    return JobSequence(
        [
            Event(
                resource=NamesSensors.ATIR,
                callable_=ATIR.write_continuous_event_handler,
                duration=timedelta(milliseconds=100),
                kwargs={
                    "event_handler":
                        ContinuousEventHandlerRepeatingNoEndSaving(
                            callable_=ATIR.write_measure.__name__
                        )
                }
            ),
            Event(
                resource=NamesSensors.NMR,
                callable_=NMR.write_continuous_event_handler,
                duration=timedelta(milliseconds=100),
                kwargs={
                    "event_handler":
                        ContinuousEventHandlerRepeatingNoEndSaving(
                            callable_=NMR.write_measure.__name__
                        )
                }
            ),

            ## segment pre
            steady_state(profiles),

            ## segment
            job_segment(profiles, start=0, end=index_refill[0]),  # 0-675
            job_segment(profiles, start=index_refill[0], end=index_refill[1]),  # 675-1349
            job_segment(profiles, start=index_refill[1], end=index_refill[2]),  # 1349-2022
            job_segment(profiles, start=index_refill[2], end=-1),  # 2022- -1

            ## segment post
            steady_state(profiles),


            Event(
                resource=NamesSensors.ATIR,
                callable_=ATIR.write_stop,
                duration=timedelta(milliseconds=100),
            ),
            Event(
                resource=NamesSensors.NMR,
                callable_=NMR.write_stop,
                duration=timedelta(milliseconds=100),
            ),

        ]
    )


def main():
    job_submitter = JobSubmitter()
    job = job_droplets()
    result = job_submitter.submit(job)
    print(result)

    # full_schedule = job_submitter.get_schedule()
    # print(full_schedule)

    print("hi")


if __name__ == "__main__":
    main()
