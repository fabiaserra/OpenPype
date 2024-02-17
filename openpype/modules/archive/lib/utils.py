
def time_elapsed(etime, exact=False):
    seconds = (int(etime % 60), "s")
    minutes = (int((etime / 60) % 60), "m")
    hours = (int((etime / 3600) % 24), "h")
    days = (int(etime / 86400), "d")

    wording = ""
    for part in [days, hours, minutes, seconds]:
        if part[0] or exact:
            wording += "{0}{1} ".format(part[0], part[1])

    return wording


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
