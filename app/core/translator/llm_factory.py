import threading
import time
# import queue
from collections import deque
from config import logger
from app.core.utils import TranslatorStatus
from app.core.utils.console_progress import console_progress


def done_f(obj):
    # print("DONE" + str(obj))
    pass


def all_done_f(obj):
    pass


class LLMFactory:
    def __init__(
        self, work_num,
        work_func:callable,
        done_func=done_f,
        all_done_func=all_done_f
    ) -> (None):
        """生成llm的工厂类
        Args:
            work_num (int): 工作线程数
            work_func (function): 处理Job的函数
            done_func (function, optional): 单个job处理完成回调. Defaults to done_f.
            all_done_func (function, optional): 所有job处理完成回调. Defaults to all_done_f.
        """
        self.work_num = work_num
        self.work_func = work_func
        self.add_finish = False
        self.job_count = 0
        self.finish_count = 0
        self.error_count = 0
        self.job_queue = deque()
        self.done_func = done_func
        self.all_done_func = all_done_func
        self.res_obj = []
        # 存放超过重试次数被丢弃的任务
        self.failed_jobs = []
        self.lock = threading.Lock()
        self.workers = []
        self.progress_label = "Current"
        self.progress_total_weight = 0
        self.progress_completed_weight = 0

    def reset(self):
        self.job_count = 0
        self.finish_count = 0
        self.error_count = 0
        self.job_queue.clear()
        self.res_obj.clear()
        self.add_finish = False
        self.progress_total_weight = 0
        self.progress_completed_weight = 0
        console_progress.clear_file()
        
    def add_jobs(self, objs: list):
        self.lock.acquire()
        self.job_count += len(objs)
        for j in objs:
            self.job_queue.append(j)
            self.progress_total_weight += self._get_job_weight(j)
        self.lock.release()
        self._render_progress()

    def set_progress_context(
        self,
        label: str,
    ):
        self.lock.acquire()
        self.progress_label = label or "Current"
        current_total = self.progress_total_weight
        self.lock.release()
        console_progress.set_file(self.progress_label, current_total)

    def reset_job(self, job: list, add_err_num: bool = True):
        self.lock.acquire()
        if add_err_num:
            job.err_time += 1
        if job.err_time <= 3:
            self.job_queue.append(job)
        else:
            self.error_count += 1
            self.progress_completed_weight += self._get_job_weight(job)
            # 记录到 failed_jobs 以便外部采集与重试
            try:
                self.failed_jobs.append(job)
            except Exception:
                pass
            logger.error(f"解析JOB超过最大重试次数，跳过JOB：{job}")
        self.lock.release()
        self._render_progress()

    def set_finish(self, add_finish):
        self.lock.acquire()
        self.add_finish = add_finish
        self.lock.release()

    def abort(self, job=None, reason: str = ""):
        self.lock.acquire()
        if job is not None:
            self.error_count += 1
            self.progress_completed_weight += self._get_job_weight(job)
            try:
                self.failed_jobs.append(job)
            except Exception:
                pass
        while len(self.job_queue) != 0:
            pending_job = self.job_queue.popleft()
            self.error_count += 1
            self.progress_completed_weight += self._get_job_weight(pending_job)
            try:
                self.failed_jobs.append(pending_job)
            except Exception:
                pass
        self.add_finish = True
        self.lock.release()
        self._render_progress()
        if reason:
            logger.error(reason)

    def get_job(self):
        self.lock.acquire()
        if len(self.job_queue) != 0:
            j = self.job_queue.popleft()
            self.lock.release()
            return j
        else:
            self.lock.release()
            return None

    def done_job(self, job):
        self.lock.acquire()
        self.finish_count += 1
        self.progress_completed_weight += self._get_job_weight(job)
        self.res_obj.append(job)
        isF = self.isFinish()
        self.lock.release()

        self.done_func(job)
        if isF:
            console_progress.clear_file()
            self.all_done_func(self.res_obj)

    def isFinish(self):
        return (
            len(self.job_queue) == 0
            and self.add_finish
            and self.finish_count + self.error_count == self.job_count
        )

    def start_work(self):
        self._render_progress()
        for i in range(self.work_num):
            self.workers.append(
                threading.Thread(target=kimi_work,
                                 args=(self, self.work_func,
                                       i,
                                       ))
            )
        for w in self.workers:
            w.start()
        for w in self.workers:
            w.join()
        self.workers.clear()
        if self.isFinish():
            console_progress.clear_file()

    def isAllDone(self):
        return self.finish_count == self.job_count

    def _progress_snapshot(self):
        current_total = self.progress_total_weight
        current_completed = self.progress_completed_weight
        return {
            "current_total": current_total,
            "current_completed": current_completed,
            "errors": self.error_count,
            "queued": len(self.job_queue),
            "current_label": self.progress_label,
        }

    def _render_progress(self):
        self.lock.acquire()
        snapshot = self._progress_snapshot()
        self.lock.release()

        current_total = snapshot["current_total"]
        if current_total <= 0:
            return

        console_progress.update_file(
            snapshot["current_completed"],
            snapshot["current_total"],
            snapshot["errors"],
            snapshot["queued"],
            snapshot["current_label"],
        )

    def _get_job_weight(self, job) -> int:
        if hasattr(job, "need_translate"):
            return 1 if getattr(job, "need_translate", False) else 0
        if hasattr(job, "jobs") and isinstance(job.jobs, list):
            return max(1, len(job.jobs))
        return 1

# 定义消费者函数
def kimi_work(factory: LLMFactory, work_func,
              work_id):
    """
    定义消费者函数
    factory： 工厂对象
    work_func：处理函数（输入job list和work_id）输出job_list和成功标志
    check_func: 检查是否可以拼接更多job,输出 布尔值
    """
    def __sleep(second: int):
        wake_up_time = time.time()+second
        while time.time() < wake_up_time:
            time.sleep(1)
            if factory.isFinish():
                break
    while not factory.isFinish():
        job = factory.get_job()
        if job is None:
            time.sleep(1)
            continue
        logger.debug(f"线程{work_id}，正在解析：{job}")
        res, kimi_status = work_func(job, work_id)
        # if kimi_status != TranslatorStatus.SUCCESS:
        #     logger.warning(f"线程{work_id}，获得结果失败，暂停5秒，重新处理JOBS")
        #     factory.reset_job(job, True)
        #     __sleep(5)
        if kimi_status == TranslatorStatus.FAILURE:
            logger.warning(f"线程{work_id}，获得结果失败,重新处理JOBS") 
            factory.reset_job(job, True)
            # __sleep(120)
        elif kimi_status == TranslatorStatus.FATAL:
            logger.error(f"线程{work_id}，遇到不可恢复错误，停止剩余任务")
            factory.abort(job, reason=f"线程{work_id} 遇到不可恢复错误，任务已提前终止")
            return
        elif kimi_status == TranslatorStatus.WAITING:
            logger.warning(f"线程{work_id}，要求超时等待，暂停1分30秒，重新处理JOBS")
            factory.reset_job(job, False)
            __sleep(90)
        else:
            factory.done_job(res)
