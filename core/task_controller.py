import asyncio
from typing import Dict, Optional

class TaskController:
    """Controls execution state of a background video pipeline task."""
    def __init__(self, task_id: str):
        self.task_id = task_id
        self.is_paused = False
        self.is_stopped = False
        self.pause_event = asyncio.Event()
        self.pause_event.set() # Unpaused initially

    def pause(self):
        self.is_paused = True
        self.pause_event.clear()

    def resume(self):
        self.is_paused = False
        self.pause_event.set()

    def stop(self):
        self.is_stopped = True
        self.pause_event.set() # Release wait if paused so loop can terminate

# In-memory registry of active task controllers
task_controllers: Dict[str, TaskController] = {}

def get_task_controller(task_id: str) -> TaskController:
    if task_id not in task_controllers:
        task_controllers[task_id] = TaskController(task_id)
    return task_controllers[task_id]

def remove_task_controller(task_id: str):
    task_controllers.pop(task_id, None)
