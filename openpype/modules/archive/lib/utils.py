
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


def format_bytes(size):
    # 2**10 = 1024
    power = 1024
    n = 0
    power_labels = {0 : 'bytes', 1: 'KB', 2: 'MB', 3: 'GB', 4: 'TB'}
    while size > power:
        size /= power
        n += 1
    return f"{round(size, 2)} {power_labels[n]}"


def interp(x, x1, y1, x2, y2):
    """Perform linear interpolation.

    It's easier to use numpy.interp but to avoid adding the
    dependency we are adding this simple function

    Args:
        x (float): The x-value to interpolate.
        x1 (float): The x-value of the first point.
        y1 (float): The y-value of the first point.
        x2 (float): The x-value of the second point.
        y2 (float): The y-value of the second point.

    Returns:
        float: The interpolated y-value.
    """
    return y1 + (x - x1) * (y2 - y1) / (x2 - x1)
