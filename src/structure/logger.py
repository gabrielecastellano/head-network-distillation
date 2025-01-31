import datetime
import time
from collections import defaultdict, deque

import torch
import torch.distributed as dist

from utils import main_util


class SmoothedValue(object):
    """Track a series of values and provide access to smoothed values over a
    window or the global series average.
    """

    def __init__(self, window_size=20, fmt=None):
        if fmt is None:
            fmt = "{median:.4f} ({global_avg:.4f})"
        self.deque = deque(maxlen=window_size)
        self.total = 0.0
        self.count = 0
        self.fmt = fmt

    def update(self, value, n=1):
        self.deque.append(value)
        self.count += n
        self.total += value * n

    def synchronize_between_processes(self):
        """
        Warning: does not synchronize the deque!
        """
        if not main_util.is_dist_avail_and_initialized():
            return
        t = torch.tensor([self.count, self.total], dtype=torch.float64, device='cuda')
        dist.barrier()
        dist.all_reduce(t)
        t = t.tolist()
        self.count = int(t[0])
        self.total = t[1]

    @property
    def median(self):
        d = torch.tensor(list(self.deque))
        return d.median().item()

    @property
    def avg(self):
        d = torch.tensor(list(self.deque), dtype=torch.float32)
        return d.mean().item()

    @property
    def global_avg(self):
        return self.total / self.count if self.count else 0

    @property
    def max(self):
        return max(self.deque) if self.count else 0

    @property
    def value(self):
        return self.deque[-1] if self.count else 0

    def __str__(self):
        return self.fmt.format(
            median=round_at_significant(self.median, 5),
            avg=round_at_significant(self.avg, 5),
            global_avg=round_at_significant(self.global_avg, 5),
            max=round_at_significant(self.max, 5),
            value=round_at_significant(self.value, 5))


class CtrValue(object):
    """
    Track a counter and provide access to fraction value over a global window.
    """

    def __init__(self, window_size=20, fmt=None):
        if fmt is None:
            fmt = "{median:.4f} ({global_avg:.4f})"
        self.c_deque = deque(maxlen=window_size)
        self.t_deque = deque(maxlen=window_size)
        self.total = 0
        self.count = 0
        self.fmt = fmt

    def update(self, count, total):
        self.c_deque.append(count)
        self.t_deque.append(total)
        self.count += count
        self.total += total

    def synchronize_between_processes(self):
        """
        Warning: does not synchronize the deque!
        """
        if not main_util.is_dist_avail_and_initialized():
            return
        t = torch.tensor([self.count, self.total], dtype=torch.float64, device='cuda')
        dist.barrier()
        dist.all_reduce(t)
        t = t.tolist()
        self.count = int(t[0])
        self.total = t[1]

    @property
    def median(self):
        dc = torch.tensor(list(self.c_deque))
        dt = torch.tensor(list(self.t_deque))
        d = dc / dt
        return d.median().item()

    @property
    def avg(self):
        dc = torch.tensor(list(self.c_deque), dtype=torch.float32)
        dt = torch.tensor(list(self.t_deque), dtype=torch.float32)
        d = dc / dt
        return d.mean().item()

    @property
    def global_avg(self):
        return self.count / self.total if self.total else 0

    @property
    def max(self):
        dc = torch.tensor(list(self.c_deque), dtype=torch.float32)
        dt = torch.tensor(list(self.t_deque), dtype=torch.float32)
        d = dc / dt
        return max(d)

    @property
    def value(self):
        return self.c_deque[-1] if self.total else 0

    def __str__(self):
        return self.fmt.format(
            median=round_at_significant(self.median, 5),
            avg=round_at_significant(self.avg, 5),
            global_avg=round_at_significant(self.global_avg, 5),
            max=round_at_significant(self.max, 5),
            value=round_at_significant(self.value, 5))


class MetricLogger(object):
    def __init__(self, delimiter="\t"):
        self.meters = defaultdict(SmoothedValue)
        self.counters = defaultdict(CtrValue)
        self.delimiter = delimiter

    def update(self, **kwargs):
        for k, v in kwargs.items():
            if isinstance(v, torch.Tensor):
                v = v.item()
            assert isinstance(v, (float, int))
            self.meters[k].update(v)

    def __getattr__(self, attr):
        if attr in self.meters:
            return self.meters[attr]
        if attr in self.counters:
            return self.counters[attr]
        if attr in self.__dict__:
            return self.__dict__[attr]
        raise AttributeError("'{}' object has no attribute '{}'".format(
            type(self).__name__, attr))

    def __str__(self):
        loss_str = []
        for name, meter in self.meters.items():
            loss_str.append(
                "{}: {}".format(name, str(meter))
            )
        return self.delimiter.join(loss_str)

    def synchronize_between_processes(self):
        for meter in self.meters.values():
            meter.synchronize_between_processes()
        for counter in self.counters.values():
            counter.synchronize_between_processes()

    def add_meter(self, name, meter):
        self.meters[name] = meter

    def add_counter(self, name, counter):
        self.counters[name] = counter

    def log_every(self, iterable, print_freq, header=None, verbose=True):
        i = 0
        if not header:
            header = ''
        start_time = time.time()
        end = time.time()
        iter_time = SmoothedValue(fmt='{avg:.4f}')
        data_time = SmoothedValue(fmt='{avg:.4f}')
        space_fmt = ':' + str(len(str(len(iterable)))) + 'd'
        if torch.cuda.is_available():
            log_msg = self.delimiter.join([
                header,
                '[{0' + space_fmt + '}/{1}]',
                'eta: {eta}',
                '{meters}',
                'time: {time}',
                'data: {data}',
                'max mem: {memory:.0f}'
            ])
        else:
            log_msg = self.delimiter.join([
                header,
                '[{0' + space_fmt + '}/{1}]',
                'eta: {eta}',
                '{meters}',
                'time: {time}',
                'data: {data}'
            ])
        MB = 1024.0 * 1024.0
        for obj in iterable:
            data_time.update(time.time() - end)
            yield obj
            iter_time.update(time.time() - end)
            if i % print_freq == 0 or i == len(iterable) - 1:
                eta_seconds = iter_time.global_avg * (len(iterable) - i)
                eta_string = str(datetime.timedelta(seconds=int(eta_seconds)))
                if i == len(iterable) - 1:
                    i = len(iterable)
                if verbose:
                    if torch.cuda.is_available():
                        print(log_msg.format(
                            i, len(iterable), eta=eta_string,
                            meters=str(self),
                            time=str(iter_time), data=str(data_time),
                            memory=torch.cuda.max_memory_allocated() / MB))
                    else:
                        print(log_msg.format(
                            i, len(iterable), eta=eta_string,
                            meters=str(self),
                            time=str(iter_time), data=str(data_time)))
            i += 1
            end = time.time()
        total_time = time.time() - start_time
        total_time_str = str(datetime.timedelta(seconds=int(total_time)))
        print('{} Total time: {}'.format(header, total_time_str))


def round_at_significant(x, d):
    # return round(x, - int(floor(log10(abs(x)))) + (d - 1))
    return x
