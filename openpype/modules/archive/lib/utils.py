import os
import subprocess
import time


class TimeEstimate:

    @classmethod
    def elapsed(self, etime, exact=False):
        seconds = (int(etime % 60), "s")
        minutes = (int((etime / 60) % 60), "m")
        hours = (int((etime / 3600) % 24), "h")
        days = (int(etime / 86400), "d")

        wording = ""
        for part in [days, hours, minutes, seconds]:
            if part[0] or exact:
                wording += "{0}{1} ".format(part[0], part[1])

        return wording

    @classmethod
    def start(self):
        global start_time
        start_time = time.time()

    @classmethod
    def show(self, total, count):
        global start_time
        delta = time.time() - start_time
        seconds_left = int((delta / count) * (total - count))

        hour, remainder = divmod(seconds_left, 60 * 60)
        min, seconds = divmod(remainder, 60)

        if hour:
            result = "Time Remaining: {0} h {1} mins".format(hour, min)
        else:
            result = "Time Remaining: {0} mins".format(min)

        return result


def to_unit(size, unit="gb"):
    """
    Accepts bytes and returns them to a human readable unit.
    """

    size = float(size)
    unit = unit.strip().lower()
    if unit == "b":
        size = size
    elif unit == "kb":
        size /= 1024
    elif unit == "mb":
        size /= 1024**2
    elif unit == "gb":
        size /= 1024**3
    elif unit == "tb":
        size /= 1024**4
    elif unit == "pb":
        size /= 1024**5
    elif unit == "ex":
        size /= 1024**6

    return size


def check_size(path, unit="gb", follow_symlinks=False):
    du_args = "-sbL" if follow_symlinks else "-sb"
    _cmd = " ".join(["du", du_args, path])
    try:
        size = float(get_exitcode_stdout_stderr(_cmd).split()[0].decode("utf-8"))
    except:
        size = 0
    size = to_unit(size, unit)

    return size


def log_header(title, length=150):
    try:
        length = int(os.popen("stty size", "r").read().split()[1])
    except:
        pass

    print("\n{0:^{1}}".format(("=" * length), length))
    print("{0:^{1}}".format(title, (length)))
    print("{0:^{1}}\n".format(("=" * length), length))


def server_space():
    # Get disk space utilization
    cmd = "df | grep $DIVEHOME"
    utilization = subprocess.Popen(
        cmd, shell=True, stdout=subprocess.PIPE
    ).communicate()[0]
    usage = utilization.split()[4]
    remainder = int(utilization.split()[3])
    remainder = round(to_unit(remainder, "gb"), 1)

    return {"usage": usage, "remainder": remainder}
