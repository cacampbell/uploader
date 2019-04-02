import datetime
import os
import re
import shutil
import sys
from math import floor
import requests
from queue import Queue
from PIL import Image
from PyQt5.QtCore import QRect
from PyQt5.QtCore import pyqtSignal
from PyQt5.QtCore import pyqtSlot
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QErrorMessage
from PyQt5.QtWidgets import QFileDialog
from PyQt5.QtWidgets import QLineEdit
from PyQt5.QtWidgets import QLabel
from PyQt5.QtWidgets import QMessageBox
from PyQt5.QtWidgets import QProgressBar
from PyQt5.QtWidgets import QPushButton
from PyQt5.QtWidgets import QWidget
from PyQt5.QtCore import QObject
from PyQt5.QtCore import QThread


class BGRunner(QObject):
    result = pyqtSignal(tuple)
    done = pyqtSignal(int)

    def __init__(self, task_queue, verbose, uploader):
        super().__init__()
        self.tasks = task_queue
        self.verbose = verbose
        self.uploader = uploader

    @pyqtSlot(name="run")
    def run(self):
        if self.verbose:
            print("Running...", file=sys.stdout)

        while True:
            if not self.tasks.empty():
                if self.verbose:
                    print("Tasks not empty, continuing...", file=sys.stdout)
            else:
                print("Last task consumed, breaking...", file=sys.stdout)
                break

            (hawb, image) = self.tasks.get()
            self.uploader.hawb_number = hawb
            self.uploader.original_image_path = image
            r = self.uploader.run()
            self.result.emit((r, image))
            self.tasks.task_done()

        if self.verbose:
            print("Finished.", file=sys.stdout)

        self.done.emit(0)
        

class ImageUploader(QWidget):
    UPLOAD_URL = "http://www.shipfsp.com/idb/imagedb/uploadImage.py"
    IMAGE_CODE = 'POD'
    IMAGE_TYPE = 'HAWB'
    MIME_TYPE = 'image/TIFF'
    LOCATION = 'MIS'
    FILE_SIZE_LIMIT = 1000000  # 1 MB
    MAX_RESIZE_PASSES = 50
    THUMBNAIL_QUALITY_PROPORTION = 0.80
    TITLE = 'MyFSP Image Uploader'

    # noinspection PyUnresolvedReferences
    def __init__(self, hawb_number="", path="", silent=False, verbose=False):
        super().__init__()
        self.silent = silent
        self.verbose = verbose
        self.timestamp = datetime.datetime.now()
        self.hawb_number = hawb_number
        self.original_image_path = path
        self.bg_results = []
        self.num_bg_tasks = -1
        self.tasks_queue = Queue()
        self.runner_thread = QThread()
        self.bg_uploader = None
        self.runner = None

    def error(self, message, exception=None, silent=True):
        if not silent:
            QErrorMessage(self).showMessage(message)
        else:
            print(message, file=sys.stderr)

        if exception is not None:
            print(repr(exception), file=sys.stderr)

    def success(self, message="Successfully Uploaded"):
        if not self.silent:
            success = QMessageBox(self)
            success.setText(message)
            success.show()
        else:
            print(message, file=sys.stdout)

    def print_request(self, req):
        print('{start}{l}{vector}{l}{headers}{l}{l}{body}...{l}{end}'.format(
            start='-----------START-----------',
            vector=req.method + ' ' + req.url,
            headers=os.linesep.join('{}: {}'.format(k, v) for k, v in req.headers.items()),
            body=req.body[:100],
            l=os.linesep,
            end='------------END------------'
        ))

    def post(self, image_path):
        files = {'imageData': (os.path.basename(image_path), open(image_path, 'rb'), self.MIME_TYPE)}

        params = {
            'location': self.LOCATION,
            'imageCode': self.IMAGE_CODE,
            'imageType': self.IMAGE_TYPE,
            'mimeType': self.MIME_TYPE,
        }

        prepared_request = requests.Request(
            'POST',
            url=self.UPLOAD_URL,
            data=params,
            files=files,
        ).prepare()

        if self.verbose:
            print("Prepared Request: " + os.linesep, file=sys.stdout)
            self.print_request(prepared_request)

        response = requests.Session().send(prepared_request)

        if self.verbose:
            print("Response:" + os.linesep, response.status_code, response.reason)

        if response.status_code == requests.codes.ok:
            return True

        response.raise_for_status()
        return False

    def compress_image(self, image_path):
        passes = 0
        image_file_size = os.stat(image_path).st_size

        if self.verbose:
            print("Original File Size: {}".format(image_file_size), file=sys.stdout)

        while image_file_size > self.FILE_SIZE_LIMIT and not passes > self.MAX_RESIZE_PASSES:
            passes += 1
            with Image.open(image_path) as img:
                downsize = tuple([int(floor(x * self.THUMBNAIL_QUALITY_PROPORTION)) for x in img.size])
                img.thumbnail(downsize, Image.ANTIALIAS)
                img.save(image_path)

            image_file_size = os.stat(image_path).st_size

            if self.verbose:
                print("Pass {}: Compressed Image Size: {}".format(
                    passes,
                    image_file_size
                ), file=sys.stdout)

    def upload_image(self, image_path):
        try:
            return self.post(image_path)
        except requests.ConnectionError as connection_error:
            self.error("Could not connect to the Image Database", connection_error, silent=True)
        except requests.Timeout as timeout:
            self.error("Timed out trying to connect to the Image Database", timeout, silent=True)
        except requests.exceptions.HTTPError as httpe:
            if httpe.response.status_code == 500 and 'SizeLimitExceededException:' in httpe.response.text:
                self.compress_image(image_path)
                return self.upload_image(image_path)
            else:
                self.error("Server Response: Error {}".format(httpe.response.status_code), httpe, silent=True)
        except requests.exceptions.RequestException as request_error:
            self.error("An error occurred while preparing the request for the Image Database", request_error, silent=True)
        return False

    def queue_images_for_upload(self, image_paths):
        self.num_bg_tasks = len(image_paths)
        self.bg_results = []
        [self.tasks_queue.put((self.hawb_number, x)) for x in image_paths]
        return 0

    def convert_image(self, image_path):
        if image_path.lower().endswith('.tif') or image_path.lower().endswith('.tiff'):
            return image_path

        path, ext = os.path.splitext(image_path)
        converted_image_path = path + ".tif"

        if self.verbose:
            print("Converted Image Path: {}".format(converted_image_path, file=sys.stdout))

        try:
            with Image.open(image_path) as final:
                final.save(converted_image_path, format="TIFF", quality=85)

            return converted_image_path
        except (OSError, IOError) as os_error:  # IOError is an alias for OSError in Python3
            self.error("OS Error during conversion", os_error, silent=True)
        return False

    def examine_bg_results(self):
        failures = [x[1] for x in self.bg_results if x[0] != 0]
        num = len(failures)

        if num > 0:
            self.error("The following files failed to upload: {}".format(', '.join(failures)), silent=self.silent)
        elif num < 0:
            self.error("Could not upload file {} to MyFSP".format(self.original_image_path), silent=self.silent)
        else:
            self.success("Successfully uploaded {}".format(self.original_image_path))

        if not self.silent:
            self.enable_controls()

        return num

    def wait_on_uploader_thread(self):
        if self.tasks_queue: self.tasks_queue.join()
        if self.runner_thread: self.runner_thread.quit()
        if self.runner_thread: self.runner_thread.wait()

    def get_all_files(self, directory):
        paths = []

        for root, directories, filenames in os.walk(directory):
            for filename in filenames:
                abs_filename = os.path.join(root, filename)

                if self.verbose:
                    print("Found file: {}".format(abs_filename), file=sys.stdout)

                paths += [abs_filename]

        return [x for x in paths if os.path.isfile(x)]

    def start_uploader_thread(self):
        self.bg_uploader = ImageUploader(
            hawb_number=self.hawb_number,
            path=self.original_image_path,
            silent=True,  # No need for windows here
            verbose=self.verbose
        )  # This QWidget has the Singleton QApplication as its parent.

        self.runner = BGRunner(self.tasks_queue,
                 verbose=self.verbose,
                 uploader=self.bg_uploader)

        self.runner_thread.started.connect(self.runner.run)
        self.runner.result[tuple].connect(self.result_listener)
        self.runner.done[int].connect(self.finished_bg_work)
        self.runner.moveToThread(self.runner_thread)

        if not self.silent:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(0)

        self.runner_thread.start()

    def process_image(self, path):
        renamed_path = self.rename_path(path)
        if renamed_path:
            converted_path = self.convert_image(renamed_path)
            if converted_path:
                result = self.upload_image(converted_path)
                if result:
                    self.success("Successfully uploaded: {}".format(self.original_image_path))
                    return 0
                self.error("{} upload failed, could not upload {}".format(self.original_image_path, converted_path), silent=self.silent)
                return 1
            self.error("{} upload failed, could not convert {}".format(self.original_image_path, renamed_path), silent=self.silent)
            return 1
        self.error("{} upload failed, could not rename {}".format(self.original_image_path, path), silent=self.silent)
        return 1

    def process_images(self, images_directory=""):
        # For each file found in the directory, attempt to rename and then convert it, filter away failures
        image_paths = self.get_all_files(images_directory)
        renamed_image_paths = [self.rename_path(path=x) for x in image_paths]
        renamed_image_paths = [f for f in renamed_image_paths if f]

        if len(renamed_image_paths) == 0:
            self.error(
                "Upload of {} failed. There were no more images to process after the renaming step. Please check the uploader log for previous errors.".format(
                    self.original_image_path), silent=self.silent)
            return 1

        converted_image_paths = [self.convert_image(x) for x in renamed_image_paths]
        converted_image_paths = [f for f in converted_image_paths if f]

        if len(converted_image_paths) == 0:
            self.error(
                "Upload of {} failed. There were no more images to process after the conversion step. Please check the uploader log for previous errors.".format(
                    self.original_image_path), silent=self.silent)
            return 1

        # Start the runner thread, already connected to waiting uploader worker, then queue images to be uploaded
        self.start_uploader_thread()
        val = self.queue_images_for_upload(converted_image_paths)

        if self.silent:
            self.wait_on_uploader_thread()
            return self.examine_bg_results()  # No UI: wait for uploading to complete

        return val  # UI: watch progress bar

    def get_page_images(self, path=""):
        from pdf2image import convert_from_path

        images = convert_from_path(path)
        images_directory, ext = os.path.splitext(path)
        basename = os.path.basename(images_directory)

        try:
            os.makedirs(images_directory)
        except FileExistsError:
            try:
                os.removedirs(images_directory)
            except OSError as err:
                if err.errno == 39:  # Directory not empty
                    shutil.rmtree(images_directory)

            os.makedirs(images_directory)

        try:
            for index, image in enumerate(images):
                page_path = os.path.join(images_directory, (basename + "_page{}.tif".format(index + 1)))
                image.save(page_path, 'TIFF')

                if self.verbose:
                    print("Saved page: {}".format(page_path))

            return images_directory
        except (IOError, OSError) as os_error:
            self.error("Cannot convert PDF pages into images", os_error, silent=True)
            return None

    def get_new_filename(self, hawb, filename):
        # format h<HAWB#>.YYYYMMDD-HHMMSS.tif expected when trying to grab hawb number by name
        name = ''  # If no original filename, don't rename (such as with directories)
        if filename != '':
            page_search = re.search('_page\d{1,6}(?!_page)', filename)  # _pageX not followed by _page
            name = "h{h}-{d}-{t}{page}{ext}".format(
                h=hawb,
                d=self.timestamp.strftime("%Y%m%d"),
                t=self.timestamp.strftime("%H%M%S"),
                page=page_search.group(0) if page_search else '',
                ext=os.path.splitext(filename)[1] if os.path.splitext(filename)[1] != '' else ''
            )

            if self.verbose:
                print("New image name: {}".format(name), file=sys.stdout)
        return name

    def rename_path(self, path):
        directory, filename = os.path.split(path)
        new_filename = self.get_new_filename(self.hawb_number, filename)
        renamed_path = os.path.join(directory, new_filename)

        if self.verbose:
            print("Renamed path: {}".format(renamed_path), file=sys.stdout)

        def rename():
            try:
                os.renames(path, renamed_path)
                return renamed_path
            except OSError:  # File or Directory that is some combination of present, not empty
                shutil.rmtree(renamed_path)
                os.renames(path, renamed_path)
                return renamed_path

        try:
            return rename()
        except Exception as err:
            self.error("Could not rename {} to {}".format(path, renamed_path), silent=True, exception=err)
            return False

    def disable_controls(self):
        try:
            self.hawb_number_field.setDisabled(True)
            self.path_field.setDisabled(True)
            self.submit.setDisabled(True)
            self.browse.setDisabled(True)
            self.progress_bar.setValue(100)
            self.progress_bar.setRange(0, 0)
        except AttributeError as err:
            self.error("AttributeError while disabling controls", silent=self.silent, exception=err)

    def enable_controls(self):
        try:
            self.hawb_number_field.setDisabled(False)
            self.path_field.setDisabled(False)
            self.submit.setDisabled(False)
            self.browse.setDisabled(False)
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(0)
        except AttributeError as err:
            self.error("AttributeError while enabling controls", silent=self.silent, exception=err)

    def run(self):
        def call_and_enable(function, path):
            result = function(path)

            if not self.silent:
                self.enable_controls()

            return result

        path = os.path.abspath(self.original_image_path)

        if not os.path.exists(path):
            self.error("Does not exist: {}".format(path), silent=self.silent)
            return 1
        if not self.silent:
            self.disable_controls()
        if path.lower().endswith(".pdf"):
            path = self.get_page_images(path)
        if os.path.isdir(path):
            return self.process_images(path)
        elif os.path.isfile(path):
            return call_and_enable(self.process_image, path)
        return 1

    def closeEvent(self, event):
        if not self.silent:
            self.disable_controls()
            self.setWindowTitle(self.TITLE + " (Closing...)")
        self.wait_on_uploader_thread()
        event.accept()

    @pyqtSlot(name="submit_upload_slot")
    def submit_upload_slot(self):
        hawb_number = self.hawb_number_field.text()
        path = self.path_field.text()

        if hawb_number == "" or not hawb_number:
            self.error("Please enter a HAWB number", silent=False)
            return 1

        if path == "" or not path:
            self.error("Please enter a file path", silent=False)
            return 1

        self.hawb_number = hawb_number
        self.original_image_path = path
        self.run()

    @pyqtSlot(name="select_file_slot")
    def select_file_slot(self):
        options = QFileDialog.Options()

        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Select Image to Upload",
            "",
            "All Files (*)",
            options=options
        )

        if filename:
            self.path_field.setText(filename)

    @pyqtSlot(tuple, name='result_listener')
    def result_listener(self, tuple):
        self.bg_results += [tuple]

        if not self.silent:
            progress = (len(self.bg_results) / self.num_bg_tasks) * 100
            self.progress_bar.setValue(progress)

    @pyqtSlot(int, name="finished_bg_work")
    def finished_bg_work(self, int):
        self.examine_bg_results()

    def init_gui(self, hawb_number="", image_path=""):
        if not hawb_number or hawb_number == "":
            hawb_number = self.hawb_number

        if not image_path or image_path == "":
            image_path = self.original_image_path

        geometry = QRect(400, 400, 500, 150)
        self.setWindowTitle(self.TITLE)

        self.setGeometry(geometry)
        self.setFixedSize(500, 150)
        self.setWindowTitle("MyFSP Image Uploader")
        self.setWindowIcon(QIcon('resources/fsp_logo.png'))

        # Labels
        self.hawb_number_label = QLabel(self)
        self.path_label = QLabel(self)
        self.hawb_number_label.setText("HAWB #: ")
        self.path_label.setText("Path: ")

        # Line Edits
        self.hawb_number_field = QLineEdit(self)
        self.path_field = QLineEdit(self)
        self.hawb_number_field.setText(hawb_number)
        self.path_field.setText(image_path)

        # Buttons
        self.browse = QPushButton(self)
        self.submit = QPushButton(self)
        self.browse.setText("Browse")
        self.submit.setText("Upload")
        self.browse.clicked.connect(self.select_file_slot)
        self.submit.clicked.connect(self.submit_upload_slot)

        # Progress Bar
        self.progress_bar = QProgressBar(self)

        # Manage Component Layout
        # Approximates rows and columns
        self.hawb_number_label.resize(90, 30)
        self.path_label.resize(90, 30)
        self.hawb_number_field.resize(300, 30)
        self.path_field.resize(300, 30)
        self.browse.resize(90, 30)
        self.submit.resize(490, 30)
        self.progress_bar.resize(490, 30)
        self.hawb_number_label.move(5, 10)
        self.path_label.move(5, 45)
        self.submit.move(5, 80)
        self.hawb_number_field.move(95, 10)
        self.path_field.move(95, 45)
        self.browse.move(405, 45)
        self.progress_bar.move(5, 115)
