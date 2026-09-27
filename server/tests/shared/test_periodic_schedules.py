"""Every periodic schedule the worker runs points at a task the worker registered.

`@app.periodic` sits on top of `@app.task(name=...)` and does NOT take a name of
its own: procrastinate's `periodic_decorator` receives the `Task` object that
`@app.task` built, `PeriodicRegistry.register_task` keys the schedule by
`task.name`, and `PeriodicDeferrer.defer_jobs` enqueues `task.configure(...)` —
the very same task. So the enqueued name is the registered name, and the
`queue=` has to be repeated on `periodic` only because `configure_task` reads the
options it is handed.

This is the invariant that makes the sweep actually run, and nothing else in the
suite pins it: every job test calls the coroutine directly, so a schedule that
named something unregistered would keep every test green while the worker never
runs it. It covers all three modules with periodic jobs (alerts every 5 min,
weather every 3 h and daily, irrigation daily) because a schedule is only as good
as the name it enqueues, whoever wrote it.
"""

from __future__ import annotations

import techcamp.worker  # noqa: F401  (imported for the side effect: registers the tasks)
from techcamp.shared.jobs import app


def test_every_periodic_schedule_enqueues_a_task_the_worker_registered() -> None:
    periodic = app.periodic_registry.periodic_tasks

    assert periodic, "the worker registers no periodic task at all"

    for (task_name, periodic_id), scheduled in periodic.items():
        assert scheduled.task.name == task_name, (
            f"the schedule for {task_name!r} (periodic_id {periodic_id!r}) enqueues "
            f"{scheduled.task.name!r}, which the worker does not run"
        )
        assert task_name in app.tasks, (
            f"the schedule {task_name!r} names a task that was never registered"
        )
        assert scheduled.configure_kwargs.get("queue") == app.tasks[task_name].queue


def test_the_node_health_sweep_runs_every_five_minutes() -> None:
    """docs/10 §3 `m[cada 5 min: salud de nodos]`, on the queue the worker listens to."""
    scheduled = {
        task_name: entry
        for (task_name, _periodic_id), entry in app.periodic_registry.periodic_tasks.items()
    }

    assert "alerts.sweep_node_health" in scheduled
    assert scheduled["alerts.sweep_node_health"].cron == "*/5 * * * *"
