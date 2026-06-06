import shutil
import sys
import threading


class ConsoleProgress:
    def __init__(self):
        self.lock = threading.Lock()
        self.is_tty = sys.stderr.isatty()
        self.total_label = "Total"
        self.total_completed = 0
        self.total_total = 0
        self.file_label = ""
        self.file_completed = 0
        self.file_total = 0
        self.file_errors = 0
        self.file_queued = 0
        self._last_width = 0
        self._last_rendered_line = ""
        self._last_non_tty_signature = None

    def set_total(self, total: int, label: str = "Total"):
        with self.lock:
            self.total_label = label or "Total"
            self.total_total = max(total, 0)
            self.total_completed = 0
        self.render()

    def advance_total(self, step: int = 1):
        with self.lock:
            self.total_completed = min(self.total_total, self.total_completed + max(step, 0))
        self.render()

    def set_file(self, label: str, total: int):
        with self.lock:
            self.file_label = label or "File"
            self.file_total = max(total, 0)
            self.file_completed = 0
            self.file_errors = 0
            self.file_queued = 0
        self.render()

    def update_file(self, completed: int, total: int, errors: int, queued: int, label: str = ""):
        with self.lock:
            if label:
                self.file_label = label
            self.file_completed = max(completed, 0)
            self.file_total = max(total, 0)
            self.file_errors = max(errors, 0)
            self.file_queued = max(queued, 0)
        self.render()

    def clear_file(self):
        with self.lock:
            self.file_label = ""
            self.file_completed = 0
            self.file_total = 0
            self.file_errors = 0
            self.file_queued = 0
        self.render()

    def clear_all(self):
        with self.lock:
            self.total_completed = 0
            self.total_total = 0
            self.file_label = ""
            self.file_completed = 0
            self.file_total = 0
            self.file_errors = 0
            self.file_queued = 0
        self._clear_line()

    def render(self):
        with self.lock:
            total_line = self._format_line(self.total_label, self.total_completed, self.total_total)
            file_line = ""
            if self.file_label and self.file_total > 0:
                file_line = self._format_line(
                    self.file_label,
                    self.file_completed,
                    self.file_total,
                    self.file_errors,
                    self.file_queued,
                )
            line = total_line if not file_line else f"{total_line} | {file_line}"
            line = self._fit_to_terminal(line)
            signature = self._build_signature()

        if not line:
            self._clear_line()
            return

        if not self.is_tty:
            if signature == self._last_non_tty_signature:
                return
            self._last_non_tty_signature = signature
            sys.stderr.write(line + "\n")
            sys.stderr.flush()
            return

        if line == self._last_rendered_line:
            return
        self._last_rendered_line = line
        self._last_width = max(self._last_width, len(line))
        sys.stderr.write(("\r" + line).ljust(self._last_width + 1))
        sys.stderr.flush()

    def _clear_line(self):
        if self._last_width <= 0:
            return
        sys.stderr.write("\r" + (" " * self._last_width) + "\r")
        sys.stderr.flush()
        self._last_width = 0
        self._last_rendered_line = ""

    def _format_line(self, label: str, completed: int, total: int, errors: int = 0, queued: int = 0) -> str:
        bar_width = 30
        ratio = min(1.0, completed / total) if total else 1.0
        filled = int(bar_width * ratio)
        bar = "#" * filled + "-" * (bar_width - filled)
        suffix = f" err={errors} queued={queued}" if errors or queued or label.startswith("File") else ""
        return f"{label} [{bar}] {completed}/{total} ({ratio * 100:5.1f}%){suffix}"

    def _fit_to_terminal(self, line: str) -> str:
        terminal_width = shutil.get_terminal_size(fallback=(120, 24)).columns
        if terminal_width <= 0 or len(line) <= terminal_width:
            return line

        overflow = len(line) - terminal_width
        file_prefix = self.file_label
        if not file_prefix or file_prefix not in line:
            return line[: max(terminal_width - 1, 0)]

        min_label_width = 18
        current_label_width = len(file_prefix)
        if current_label_width <= min_label_width:
            return line[: max(terminal_width - 1, 0)]

        keep_width = max(min_label_width, current_label_width - overflow - 3)
        short_label = self._ellipsize_middle(file_prefix, keep_width)
        return line.replace(file_prefix, short_label, 1)

    def _ellipsize_middle(self, text: str, width: int) -> str:
        if width <= 3 or len(text) <= width:
            return text[:width]
        left = (width - 3) // 2
        right = width - 3 - left
        return f"{text[:left]}...{text[-right:]}"

    def _build_signature(self):
        file_bucket = -1
        if self.file_total > 0:
            file_bucket = min(20, int((self.file_completed * 20) / self.file_total))
        return (
            self.total_completed,
            self.total_total,
            self.file_label,
            self.file_total,
            file_bucket,
            self.file_errors,
        )


console_progress = ConsoleProgress()
