"""Job Object di Windows: i processi figli muoiono insieme a Jarvis.

Senza questo, se il core di Jarvis si chiudesse in modo brusco, i motori (audio.cpp, llama.cpp)
resterebbero vivi a occupare VRAM e porte. Con JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE il sistema
operativo li termina quando si chiude l'ultimo handle del job, cioè quando muore il nostro processo.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes

log = logging.getLogger("jarvis.winjob")

_job_handle = None


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes",
                                                 "OtherBytes")]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimits), ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


_KILL_ON_JOB_CLOSE = 0x2000
_EXTENDED_LIMIT_INFORMATION = 9
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001


def _job() -> int | None:
    global _job_handle
    if sys.platform != "win32":
        return None
    if _job_handle is None:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        h = k32.CreateJobObjectW(None, None)
        if not h:
            raise ctypes.WinError(ctypes.get_last_error())
        info = _ExtendedLimits()
        info.BasicLimitInformation.LimitFlags = _KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(wintypes.HANDLE(h), _EXTENDED_LIMIT_INFORMATION,
                                           ctypes.byref(info), ctypes.sizeof(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        _job_handle = h
    return _job_handle


def adopt(pid: int) -> bool:
    """Lega un processo figlio al ciclo di vita di Jarvis. Restituisce False se non è stato possibile."""
    if sys.platform != "win32":
        return False
    try:
        job = _job()
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        hp = k32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
        if not hp:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not k32.AssignProcessToJobObject(wintypes.HANDLE(job), wintypes.HANDLE(hp)):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            k32.CloseHandle(wintypes.HANDLE(hp))
        return True
    except OSError as e:
        log.warning("impossibile legare il processo %s al job di Jarvis: %s", pid, e)
        return False
