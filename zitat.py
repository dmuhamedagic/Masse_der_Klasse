#!/usr/bin/python
# -*- coding: utf-8 -*-
#

import serial
import time
import sys
import subprocess
import os
import ConfigParser
import random
import syslog

min_rpt = 3 # stable weight read at least 3 times
max_dis = 30 # tolerate 30kg difference for stable weight
read_sleep = 2 # sleep between readings
zitat_show_time = 5 # 900
banner_wait = 5 # 20 ?
no_zitat_time = 5 # 900
weight_display = 0 # display weight only?
weight_scale = 100 # the scale reports skewed weight (in pct)?
min_weight_display = 190 # min weight to display

if "ZITATDIR" in os.environ:
    wd = os.environ["ZITATDIR"]
else:
    wd = '/'.join([os.environ["HOME"], "skulptura"])

masse_zitaten = '/'.join([wd, "masse_der_klasse.txt"])
ini_f = '/'.join([wd, "zitat.rc"])

DEFAULTS = {
    'z': {
        'min_rpt': '3', # stable weight read at least 3 times
        'max_dis': '30', # tolerate 30kg difference for stable weight
        'read_sleep': '2', # sleep between readings
        'zitat_show_time': '5', # 900
        'banner_wait': '5', # 20 ?
        'no_zitat_time': '5', # 900
        'random': '1', # random (0 or non-0)
        'weight_display': '0', # just display weight (0 or non-0)
        'weight_scale': '100', # the scale reports skewed weight (in pct)?
        'min_weight_display': '190', # min weight to display
    }
}

syslog.openlog(facility=syslog.LOG_LOCAL0)

def log_msg(s, prio, prio_str):
    if sys.stdin.isatty():
        print >>sys.stderr, "%s: %s: %s" % (prio_str, time.asctime(), s)
    else:
        syslog.syslog(prio, s)
def log_debug(s):
    log_msg(s, syslog.LOG_DEBUG, "DEBUG")
def log_info(s):
    log_msg(s, syslog.LOG_INFO, "INFO")
def log_err(s):
    log_msg(s, syslog.LOG_ERR, "ERROR")

def runcmd(cmd):
    log_info("running: %s" % cmd)
    subprocess.call(cmd.split())

def screen_on():
    runcmd("xset s off")
    runcmd("xset dpms force on")
    runcmd("xset dpms 0 0 0")

class Configuration(object):
    def __init__(self):
        self.opts = None
        self.ts = 0
        self.set_defaults()
    def set_defaults(self):
        self.opts = ConfigParser.SafeConfigParser()
        for section, keys in DEFAULTS.iteritems():
            self.opts.add_section(section)
            for key, opt in keys.iteritems():
                self.opts.set(section, key, opt)
    def load(self):
        if os.path.isfile(ini_f) and self.ts < os.stat(ini_f).st_mtime:
            log_info("options in %s" % ini_f)
            self.ts = os.stat(ini_f).st_mtime
            self.opts.read([ini_f,])
            return self.testconf()
        else:
            return 0
    def get(self, key):
        self.load()
        try:
            return self.opts.getint('z', key)
        except:
            log_err("unknown/bad key: %s" % key)
            return -1
    def testconf(self):
        rc = 0
        for section, keys in DEFAULTS.iteritems():
            for key, opt in keys.iteritems():
                val = self.get(key)
                if self.get(key) == -1:
                    rc = 1
                else:
                    log_info("option %s = %d" % (key,val))
        if rc == 1:
            log_info("using defaults")
            self.set_defaults()
        return rc

class ScreenMessage(object):
    '''
    sm(1) interface
    '''
    sm_fifo = '/'.join([wd, "sm.fifo"])
    def_opts = "-b Black -f LightGray -a 1"
    banner = "Masse der Klasse\n\nElvedin Klačar\n(c) 2016"
    adjust_space = "    "

    def __init__(self, opts = None):
        self.opts = opts or self.def_opts
        cmd = "/usr/games/sm %s - < %s" % (self.opts, self.sm_fifo)
        if not os.path.exists(self.sm_fifo):
            os.mkfifo(self.sm_fifo)
        self.p = subprocess.Popen(cmd, shell=True, close_fds=True)
        self.sm_f = open(self.sm_fifo, "w")
        self.showing_banner = 0
    def __exit__(self):
        self.p.terminate()
        self.p.wait()
        if os.path.exists(sm_fifo):
            os.unlink(self.sm_fifo)
    def space(self, s):
        return ''.join([self.adjust_space, s, self.adjust_space])
    def refmt(self, s, wd):
        # use par(1) to reformat
        min_width = 30
        max_width = 50
        #cmd = "fmt -w %d" % wd
        cmd = "par w%d" % wd
        proc = subprocess.Popen(cmd,
                            shell=True,
                            stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE)
        outp = proc.communicate(s)
        proc.wait()
        return outp[0].strip()
    def add_space(self, s):
        # put same spaces around
        return '\n'.join(self.space(p) for p in s.split("\n"))
    def show(self, s, do_refmt=True, addspace=True, wd=30):
        if do_refmt:
            if s.startswith('*'):
                s = s[1:]
            else:
                s = self.refmt(s, wd)
        if addspace:
            s = self.add_space(s)
        self.sm_f.write("%s\f" % s)
        log_debug("showing: >>>%s<<<" % s)
        self.sm_f.flush()
        self.showing_banner = 0
    def show_wt(self, wt):
        #self.show("%d kg" % wt)
        self.show("%d" % wt)
    def clear(self):
        self.show(" ", False)
    def show_banner(self):
        self.show(self.banner)
        self.showing_banner = 1
    def sample(self):
        cmd = "fortune | par w40"
        fortune_proc = subprocess.Popen(cmd,
                            shell=True,
                            stdout=subprocess.PIPE)
        outp = fortune_proc.communicate()
        fortune_proc.wait()
        return outp[0]

class Argeo(object):
    '''
    Argeo scale interface

    sample reading:    "US,GS,  -18384,kg"
    '''
    def_port = "/dev/ttyS0"
    read_str = b'READ\r\n'
    argeo_stable_str = "ST"

    def __init__(self, port=None, dbg=0):
        self.port = port or self.def_port
        self.ser = serial.Serial(self.port,
                baudrate=9600, bytesize=8, parity='N', stopbits=1,
                timeout=1, xonxoff=0, rtscts=0)
        self.is_debug = dbg
        self.reset()
    def is_same_wt(self, wt, prev_wt=None):
        if prev_wt is None:
            prev_wt = self.prev_wt
        return abs(wt - prev_wt) <= conf.get('max_dis')
    def is_last_wt(self, wt):
        return self.is_same_wt(wt, self.last_rpt_wt)
    def reset(self):
        self.prev_wt = 0
        self.last_rpt_wt = 0
        self.wt_cnt = 0
    def read_wt(self):
        self.ser.write(self.read_str)
        l = self.ser.readline().strip()
        if not l:
            self.debug("no reply, scale off?")
            return None
        a = l.split(",")
        #if len(a) < 4 or a[0] != self.argeo_stable_str:
        if len(a) < 4:
            self.debug("ignoring: %s" % l)
            return None
        try:
            wt = int(a[2])
        except:
            self.debug("field 3 not number: %s" % l)
            return None
        wt = int(wt * conf.get('weight_scale')/100.0)
        if self.is_same_wt(wt, 0):
            self.reset()
            return 0
        if self.is_same_wt(wt):
            self.wt_cnt += 1
            self.debug("weight %d, count %d" % (wt,
                self.wt_cnt))
        else:
            self.debug("new weight %d" % wt)
            self.prev_wt = wt
            self.wt_cnt = 0
        return wt
    def set_last_wt(self, wt):
        self.last_rpt_wt = wt
    def is_stable(self):
        return self.wt_cnt >= conf.get('min_rpt')
    def set_debug(self):
        self.is_debug = 1
    def debug(self, s):
        if self.is_debug:
            log_debug("ARGEO: %s" % s)

class Zitat(object):
    '''
    read quotes from a file
    pick quote whose weight range matches wt
    quote ends with a single dot on the line

    n1 - n2
    l1
    l2
    ...
    ln
    .
    '''
    def __init__(self, txt_f=masse_zitaten):
        self.f = None
        self.ts = 0
        self.txt_f = txt_f
        self.rnd = False
    def set_rnd(self, rnd):
        self.rnd = rnd
        random.seed()
    def rewind(self):
        self.refresh()
        self.f.seek(0)
    def refresh(self):
        if self.f and self.ts >= os.stat(self.txt_f).st_mtime:
            return
        if self.f:
            self.f.close()
        log_info("loading %s" % self.txt_f)
        self.ts = os.stat(self.txt_f).st_mtime
        self.f = open(self.txt_f, "r")
        return self.load()
    def zt_error(self, s):
        log_err("%s:%d: %s" % (self.txt_f, self.lineno, s))
    def zt_info(self, s):
        log_info("%s:%d: %s" % (self.txt_f, self.lineno, s))
    def chk_range(self, wt_range):
        a = wt_range.split("-")
        wt = 0
        try:
            if len(a) == 2 and int(a[0]) < int(a[1]):
                return True
        except:
            pass
        self.zt_error("bad range: %s" % wt_range)
        return False
    def get_next(self):
        l = ''
        # this skips empty lines
        # and finds eof
        while not l:
            l = self.f.readline()
            if not l:
                return None,None
            self.lineno += 1
            l = l.strip()
        wt_range = l
        a = []
        while True:
            l = self.f.readline().rstrip("\n")
            self.lineno += 1
            if l == ".":
                break
            a.append(l)
        return wt_range,'\n'.join(a)
    def is_in_range(self, wt_range, wt):
        a = wt_range.split("-")
        try:
            if wt >= int(a[0]) and wt <= int(a[1]):
                return True
        except:
            return False
        return False
    def find_zitat(self, wt):
        self.rewind()
        self.lineno = 1
        n = 1
        if self.rnd:
            stop_n = random.randrange(1, self.cnt)
        while True:
            (wt_range,zt) = self.get_next()
            if wt_range is None:
                return None
            if self.rnd:
                if n == stop_n:
                    return zt
            else:
                if self.is_in_range(wt_range, wt):
                    return zt
            n += 1
        return None
    def load(self):
        self.rewind()
        self.lineno = 1
        self.cnt = 0
        rc = 0
        while True:
            (wt_range,zt) = self.get_next()
            if wt_range is None:
                break
            if self.chk_range(wt_range):
                if not zt:
                    self.zt_error("empty zitat")
                    rc = 1
                else:
                    self.zt_info("good zitat for range %s" % wt_range)
                    self.cnt += 1
            else:
                rc = 1
        self.zt_info("total num: %d" % self.cnt)
        return rc

def zitat_expired(showing_time):
    return time.time()-showing_time > conf.get('zitat_show_time')
def read_pause():
    time.sleep(conf.get('read_sleep'))

def cont_display():
    log_info("continuous wait display")
    while True:
        wt = scale.read_wt()
        if wt is None:
            s = "no result"
        else:
            s = str(wt)
        sm.show(s)
        time.sleep(1)

def testconf():
    log_info("testing configuration and text")
    rc = (conf.load() | zt.refresh())
    if rc != 0:
        log_err("FAIL")
    else:
        log_info("OK")
    return rc

def testdisp(width, n=0):
    zt.rewind()
    time.sleep(1)
    i = 1
    while True:
        (wt_range,s) = zt.get_next()
        if not s:
            log_info("disp exiting")
            break
        if not n or (n and n == i):
            sm.show("%d: %s" % (i, wt_range), wd=width)
            time.sleep(3)
            sm.show(s, wd=width)
            time.sleep(9)
        i += 1

zt = Zitat()
conf = Configuration()

if len(sys.argv) > 1 and sys.argv[1] == "check":
    rc = testconf()
    sys.exit(rc)

conf.load()
sm = ScreenMessage()
screen_on()

if len(sys.argv) > 1 and sys.argv[1] == "disp":
    if len(sys.argv) > 2:
        wd = int(sys.argv[2])
    else:
        wd = 30
    while True:
        if len(sys.argv) > 3:
            testdisp(wd, n=int(sys.argv[3]))
        else:
            testdisp(wd)
    sys.exit(0)

log_info("zitat.py starting")

zt.set_rnd(conf.get('random') != 0)
scale = Argeo(dbg=1)

zitat_time = 0
showing_zitat = 0
#sm.show_banner()
#time.sleep(conf.get('banner_wait'))

if conf.get('weight_display') != 0:
    cont_display()
    sys.exit(0)

while True:
    wt = scale.read_wt()
    if wt is None or wt == 0:
        if not showing_zitat:
            sm.clear()
        read_pause()
        continue
    if showing_zitat:
        if not scale.is_last_wt(wt) and zitat_expired(zitat_time):
            showing_zitat = 0
            scale.reset()
        read_pause()
        continue
    if abs(wt) >= conf.get('min_weight_display'):
        sm.show_wt(wt)
    #sm.show_wt(scale.wt_cnt)
    if not scale.is_last_wt(wt) and scale.is_stable():
        read_pause()
        # s = sm.sample()
        s = zt.find_zitat(wt)
        if s:
            sm.show(s)
            showing_zitat = 1
            zitat_time = time.time()
            scale.set_last_wt(wt)
    read_pause()

log_info("zitat.py exiting")
# vim:ts=4:sw=4:et:fileencoding=utf-8
