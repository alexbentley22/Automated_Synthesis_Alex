import time
import threading

from scheduler.develop.event_scheduler import EventScheduler


def delay1(arg):
    """
    Example job function with a 1‑second blocking delay.

    Purpose
    -------
    - Demonstrate how an EventScheduler executes a short-running task.
    - Print timestamps to visualize scheduling and concurrency behavior.

    Parameters
    ----------
    arg : Any
        Identifier value used to distinguish concurrent calls.
    """
    print(f"{arg} delay1: start {time.time()}")
    time.sleep(1)
    print(f"{arg} delay1: end {time.time()}")


def delay2(arg):
    """
    Example job function with a 2‑second blocking delay.

    Purpose
    -------
    - Show how longer-running tasks behave under the scheduler.
    - Used to test overlapping and staggered execution timing.
    """
    print(f"{arg} delay2: start {time.time()}")
    time.sleep(2)
    print(f"{arg} delay2: end {time.time()}")


def run_threaded(job_func, *args, **kwargs):
    """
    Utility function to run a callable in a separate thread.

    Notes
    -----
    - This mirrors the non-blocking execution model used by EventScheduler.
    - Included here to illustrate threading usage explicitly.
    """
    job_thread = threading.Thread(target=job_func, args=args, kwargs=kwargs)
    job_thread.start()


def main():
    """
    Entry point for demonstrating EventScheduler behavior.

    Workflow
    --------
    1. Create and start an EventScheduler.
    2. Schedule multiple actions at absolute times.
    3. Assign the same actions to multiple argument values.
    4. Poll scheduler state until all events complete.
    5. Stop the scheduler.
    """
    sched = EventScheduler()
    sched.start()
    print("start")

    now = time.time()

    # Define actions with absolute execution times
    actions = [
        [now + 1, delay1],
        [now + 3, delay2],
        [now + 5, delay1],
        [now + 7, delay1],
    ]

    # Schedule each action twice with different arguments
    for t, action in actions:
        sched.enterabs(t, action, args=(1,))
        sched.enterabs(t, action, args=(2,))

    print("now: ", now)

    # Wait for scheduler to drain its queue
    while not sched.empty():
        print("sleep")
        time.sleep(0.3)

    print("hi")

    # Stop scheduler immediately (hard stop)
    sched.stop(hard_stop=True)
    print("end")


if __name__ == "__main__":
    main()
