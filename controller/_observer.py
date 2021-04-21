import os
import time
from typing import Callable, List
from watchdog.events import FileSystemEventHandler, FileSystemEvent
from watchdog.observers import Observer
from .logger import logger
from .utils import get_dir_regex
from .types import SnapDirPrefixType

class FileEventHandler(FileSystemEventHandler):
    def __init__(self, on_created: Callable[[FileSystemEvent], None] = None):
        super().__init__()
        self.on_created_callback = on_created

    """
    def on_any_event(self, event):
        print(event.event_type, event.src_path)
    """

    def on_created(self, event):
        print("on_created", event.src_path)
        if self.on_created_callback: self.on_created_callback(event)

    """
    def on_deleted(self, event):
        print("on_deleted", event.src_path)

    def on_modified(self, event):
        print("on_modified", event.src_path)

    def on_moved(self, event):
        print("on_moved", event.src_path)
    """

def _add_schedule(observer: Observer, path: str, on_created: Callable[[FileSystemEvent], None]):
    event_handler = FileEventHandler(on_created)
    observer.schedule(event_handler, path, recursive=True)

def run_observer(work_dir: str, target_files: List[str], on_created: Callable[[FileSystemEvent], None]):
    observer = Observer()

    def _on_target_file_created(event: FileSystemEvent):
        path = event.src_path
        basename = os.path.basename(path)
        print("test", path)

        if os.path.isfile(path) and basename in target_files:
            logger.info(f"Target file {basename} created")
            on_created(event)

    _add_schedule(observer, work_dir, _on_target_file_created)

    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()

    observer.join()
