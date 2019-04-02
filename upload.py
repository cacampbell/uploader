#!/usr/bin/env python3
import argparse
import time
import sys
import logging
from functools import wraps
from PyQt5.QtWidgets import QApplication
from PyQt5.QtWidgets import QStyleFactory
from ImageUploader import ImageUploader


def log_this(function, level=logging.INFO):
    class StreamToLogger():
        def __init__(self, logger, log_level=level):
            self.logger = logger
            self.log_level = log_level

        def write(self, buf):
            for line in buf.rstrip().splitlines():
                self.logger.log(self.log_level, line.rstrip())

        def flush(self, *args, **kwargs):
            pass  # I don't need to be flushed :blushes:

    def logging_exception(msg, *args, **kwargs):
        print(msg,
              ' '.join([str(x) for x in args]),
              ' '.join(["{}={}".format(key, kwargs.get(key)) for key in kwargs.keys()]),
              file=sys.__stderr__)

    @wraps(function)
    def log_wrapper(*args, **kwargs):
        logging.basicConfig(
            level=level,
            filename='uploader.log',
            filemode='a',
            format='[{name}] {asctime} {levelname}: {message}',
            datefmt='%d-%m-%Y %H:%M:%S',
            style='{'
        )

        stdout_l = logging.getLogger("MyFSP Image Uploader (STDOUT)")
        stderr_l = logging.getLogger("MyFSP Image Uploader (STDERR)")
        out = StreamToLogger(stdout_l)
        err = StreamToLogger(stderr_l)
        sys.stdout = out
        sys.stderr = err
        sys.excepthook = logging_exception
        return function(*args, **kwargs)

    return log_wrapper


def time_this(function):
    @wraps(function)
    def timer_wrapper(*args, **kwargs):
        before = time.clock()
        result = function(*args, **kwargs)
        after = time.clock()
        print("Program Runtime: {} seconds".format(after - before), file=sys.stdout)
        return result
    return timer_wrapper


def set_style(application, stylename):
    application.setStyle(QStyleFactory.create(stylename))


@log_this
@time_this
def run(args):
    silence = args.silent >= 1 if args.silent else False
    verbose = args.verbose >= 1 if args.verbose else False
    path = args.dropped_filename if args.dropped_filename else args.path
    app = QApplication(sys.argv)

    if args.style:
        set_style(app, args.style.capitalize())
    else:
        set_style(app, "Fusion")

    uploader = ImageUploader(
        hawb_number=args.hawb,
        path=path,
        silent=silence,
        verbose=verbose
    )

    if not silence:
        uploader.init_gui()
        uploader.show()
        return sys.exit(app.exec_())  # Run not returned; instead close with press of X in UI
    else:
        return(uploader.run())


def parse_args():
    parser = argparse.ArgumentParser(description="Upload Images to MyFSP")
    parser.add_argument(
        'dropped_filename',
        metavar="<file>",
        nargs='?',
        type=str,
        help='A path to a filename or directory to be uploaded',
    )

    parser.add_argument(
        '--hawb',
        metavar='<HAWB Number>',
        help="HAWB Number"
    )
    parser.add_argument(
        '--path',
        metavar='<path>',
        help="Full path of image file, PDF, or directory containing image files to be uploaded"
    )
    parser.add_argument(
        '-s',
        '--silent',
        action='count',
        help="Run Program in stealth mode"
    )
    parser.add_argument(
        '-v',
        '--verbose',
        action='count',
        help="Run Program in verbose mode"
    )
    parser.add_argument(
        '--style',
        metavar='<Style Name> (Options: {})'.format(", ".join(QStyleFactory.keys())),
        help="Set Program style"
    )

    return(parser.parse_args())


if __name__ == "__main__":
    args = parse_args()
    run(args)  # Decorated
