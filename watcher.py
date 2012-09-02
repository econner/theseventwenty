from argparse import ArgumentParser
from subprocess import call
import functools
import os.path
import shlex
import signal
import threading
import time

signal.signal(signal.SIGINT, signal.SIG_DFL)


SYNC_DELAY = 0.1


class Syncer(threading.Thread):
    def __init__(self, source, remote_spec):
        super(Syncer, self).__init__()

        self.source = source
        self.remote_spec = remote_spec

        self.lock = threading.Lock()
        self.last_event = 0
        self.path = None

    def add_event(self, path):
        with self.lock:
            self.last_event = time.time()
            if self.path is None:
                self.path = path
            else:
                self.path = os.path.commonprefix([self.path, path])

    def sync_cb(self):
        with self.lock:
            if not self.path or time.time() - self.last_event < SYNC_DELAY:
                return

            print 'Syncing %s' % self.path
            remote_path = self.path[len(self.source):]
            cmd = "/usr/bin/rsync -avz --delete --exclude *.pyc --exclude *.ldb --exclude .DS_Store " "%s %s/%s" % (self.path, self.remote_spec, remote_path)
            call(shlex.split(cmd))

            self.path = None

    def run(self):
        while True:
            self.sync_cb()
            time.sleep(SYNC_DELAY / 2)


def file_changed(add_event, subpath, mask):
    add_event(subpath)


parser = ArgumentParser(description="Push local changes to a remote server")
parser.add_argument("-s", dest="source", help="the directory to monitor", required=True)
parser.add_argument("remote_spec")

if __name__ == '__main__':
    options = parser.parse_args()

    syncer = Syncer(options.source, options.remote_spec)
    syncer.start()
    try:
        import fsevents
        observer = fsevents.Observer()
        stream = fsevents.Stream(functools.partial(file_changed, syncer.add_event), options.source)
        observer.schedule(stream)
        observer.run()
    except ImportError:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler

        class FileChangedHandler(FileSystemEventHandler):
            """Logs all the events captured."""
            def __init__(self, syncer):
                self.syncer = syncer

            def on_any_event(self, event):
                super(FileChangedHandler, self).on_any_event(event)
                self.syncer.add_event(event.src_path)

        observer = Observer()
        observer.schedule(
            FileChangedHandler(syncer),
            path=options.source,
            recursive=True
        )
        observer.start()