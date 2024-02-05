# ARandR -- Another XRandR GUI
# Copyright (C) 2008 -- 2011 chrysn <chrysn@fsfe.org>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""Wrapper around command line xrandr (mostly 1.2 per output features supported)"""
# pylint: disable=too-few-public-methods,wrong-import-position,missing-docstring,fixme

import os
import subprocess
import warnings
import configparser
import xml.etree.ElementTree as xmlet

from .auxiliary import (
    BetterList, Size, Position, Geometry, FileLoadError, FileSyntaxError,
    InadequateConfiguration, Rotation, ROTATIONS, NORMAL, NamedSize, wlrrot
)
from .i18n import _

class Feature:
    PRIMARY = 1

class XRandR:

    configuration = None
    state = None
    command = "xrandr"
    compositor = "openbox"

    def __init__(self, display=None, force_version=False):
        """Create proxy object and check for xrandr at `display`. Fail with
        untested versions unless `force_version` is True."""
        if os.environ.get ("WAYLAND_DISPLAY") is not None:
            self.command = "wlr-randr"
            if os.environ.get ("WAYFIRE_CONFIG_FILE") is not None:
                self.compositor = "wayfire"
            else:
                self.compositor = "labwc"

        self.environ = dict(os.environ)
        if display:
            self.environ['DISPLAY'] = display

        if self.command == 'xrandr':
            version_output = self._output("--version")
            supported_versions = ["1.2", "1.3", "1.4", "1.5"]
            if not any(x in version_output for x in supported_versions) and not force_version:
                raise Exception("XRandR %s required." %
                            "/".join(supported_versions))

        self.features = set()
        if self.command == 'xrandr':
            if " 1.2" not in version_output:
                self.features.add(Feature.PRIMARY)

        self._find_touchscreens()
        self._load_current_state()

    def _get_outputs(self):
        assert self.state.outputs.keys() == self.configuration.outputs.keys()
        return self.state.outputs.keys()
    outputs = property(_get_outputs)

    #################### calling xrandr ####################

    def _output(self, *args):
        proc = subprocess.Popen(
            (self.command,) + args,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.environ
        )
        ret, err = proc.communicate()
        status = proc.wait()
        if status != 0:
            raise Exception(self.command + " returned error code %d: %s" % (status, err))
        if err:
            warnings.warn(self.command + " wrote to stderr, but did not report an error (Message was: %r)" % err)
        return ret.decode('utf-8')

    #################### loading ####################

    def load_from_strings(self, confdata, tsdata):
        self._load_current_state()
        if self.command == 'xrandr':
            self._load_from_commandlineargs(confdata.strip())
        else:
            self._load_from_commandlineargswlr(confdata.strip())

        for output_name in self.outputs:
            self.state.outputs[output_name].touchscreen = ""
        oplist = tsdata.split(",")
        for tsop in oplist:
            if tsop != "":
                ts = tsop.split(':')
                if ts[1] != "":
                    self.state.outputs[ts[0]].touchscreen = ts[1]

    def _load_from_commandlineargs(self, commandline):
        args = BetterList(commandline.split(" "))
        if args.pop(0) != 'xrandr':
            raise FileSyntaxError()
        # first part is empty, exclude empty parts
        options = dict((a[0], a[1:]) for a in args.split('--output') if a)

        for output_name, output_argument in options.items():
            output = self.configuration.outputs[output_name]
            output_state = self.state.outputs[output_name]
            output.primary = False
            if output_argument == ['--off']:
                output.active = False
            else:
                if '--primary' in output_argument:
                    if Feature.PRIMARY in self.features:
                        output.primary = True
                    output_argument.remove('--primary')
                if len(output_argument) % 2 != 0:
                    raise FileSyntaxError()
                parts = [
                    (output_argument[2 * i], output_argument[2 * i + 1])
                    for i in range(len(output_argument) // 2)
                ]
                mode = ''
                rate = ''
                for part in parts:
                    if part[0] == '--mode':
                        mode = part[1]
                        if mode and rate:
                            for namedmode in output_state.modes:
                                if namedmode.name == mode + ' ' + rate + 'Hz':
                                    output.mode = namedmode
                                    break
                            else:
                                raise FileLoadError("Not a known mode: %s" % (mode + ' ' + rate + 'Hz'))
                    elif part[0] == '--rate':
                        rate = part[1]
                        if mode and rate:
                            for namedmode in output_state.modes:
                                if namedmode.name == mode + ' ' + rate + 'Hz':
                                    output.mode = namedmode
                                    break
                            else:
                                raise FileLoadError("Not a known mode: %s" % (mode + ' ' + rate + 'Hz'))
                    elif part[0] == '--pos':
                        output.position = Position(part[1])
                    elif part[0] == '--rotate':
                        if part[1] not in ROTATIONS:
                            raise FileSyntaxError()
                        output.rotation = Rotation(part[1])
                    else:
                        raise FileSyntaxError()
                output.active = True

    def _load_current_state(self):
        self.configuration = self.Configuration(self)
        self.state = self.State()

        if self.command == 'wlr-randr':
            screenline, items = self._read_wlr_randr()
        else:
            screenline, items = self._read_xrandr()

        self._load_parse_screenline(screenline)

        for headline, details in items:
            if headline.startswith("  "):
                continue  # a currently disconnected part of the screen i can't currently get any info out of
            if headline == "":
                continue  # noise

            headline = headline.replace(
                'unknown connection', 'unknown-connection')
            hsplit = headline.split(" ")
            output = self.state.Output(hsplit[0])
            assert hsplit[1] in (
                "connected", "disconnected", 'unknown-connection')

            output.connected = (hsplit[1] in ('connected', 'unknown-connection'))

            primary = False
            if 'primary' in hsplit:
                if Feature.PRIMARY in self.features:
                    primary = True
                hsplit.remove('primary')

            if not hsplit[2].startswith("("):
                active = True

                geometry = Geometry(hsplit[2])

                if hsplit[4] in ROTATIONS:
                    current_rotation = Rotation(hsplit[4])
                else:
                    current_rotation = NORMAL
            else:
                active = False
                geometry = None
                current_rotation = None

            output.rotations = set()
            for rotation in ROTATIONS:
                if self.command == 'wlr-randr' or rotation in headline:
                    output.rotations.add(rotation)

            currentname = None
            for detail, w, h, f in details:
                if f == "None":
                    name = detail[0]
                else:
                    name = detail[0] + f
                try:
                    size = Size([int(w), int(h)])
                except ValueError:
                    raise Exception(
                        "Output %s parse error: modename %s." % (output.name, name)
                    )
                if "*current" in detail:
                    currentname = name

                for old_mode in output.modes:
                    if old_mode.name == name:
                        if tuple(old_mode) != tuple(size):
                            warnings.warn((
                                "Supressing duplicate mode %s even "
                                "though it has different resolutions (%s, %s)."
                            ) % (name, size, old_mode))
                        break
                else:
                    # the mode is really new
                    output.modes.append(NamedSize(size, name=name))

            output.touchscreen = self._get_device_touchscreen (output.name)
            self.state.outputs[output.name] = output
            self.configuration.outputs[output.name] = self.configuration.OutputConfiguration(
                active, primary, geometry, current_rotation, currentname
            )

    def _read_xrandr(self):
        output = self._output("--verbose")
        items = []
        screenline = None
        for line in output.split('\n'):
            if line.startswith("Screen "):
                assert screenline is None
                screenline = line
            elif line.startswith('\t'):
                continue
            elif line.startswith(2 * ' '):  # [mode, width, height]
                line = line.strip()
                if line.startswith('h:'):
                    htot = int(line.split()[8])
                    line = line[-len(line):line.index(" start") - len(line)]
                    items[-1][1][-1].append(line[line.rindex(' '):])
                elif line.startswith('v:'):
                    vtot = int(line.split()[8])
                    dfreq = "{:.3f}".format (freq * 1000000 / (htot * vtot))
                    l1 = line[-len(line):line.index(" start")-len(line)]
                    items[-1][1][-1].append(l1[l1.rindex(' '):])
                    l1 = line[-len(line):line.index("Hz")-len(line)]
                    items[-1][1][-1].append(" " + dfreq + 'Hz')
                else:  # mode
                    freq = float(line.split()[2][:-3])
                    items[-1][1].append([line.split()])
            elif "disconnected" not in line:
                items.append([line, []])
        return screenline, items

    def _load_parse_screenline(self, screenline):
        assert screenline is not None
        ssplit = screenline.split(" ")

        ssplit_expect = ["Screen", None, "minimum", None, "x", None,
                         "current", None, "x", None, "maximum", None, "x", None]
        assert all(a == b for (a, b) in zip(
            ssplit, ssplit_expect) if b is not None)

        self.state.virtual = self.state.Virtual(
            min_mode=Size((int(ssplit[3]), int(ssplit[5][:-1]))),
            max_mode=Size((int(ssplit[11]), int(ssplit[13])))
        )
        self.configuration.virtual = Size(
            (int(ssplit[7]), int(ssplit[9][:-1]))
        )

    #################### saving ####################

    def get_config_strings(self):
        ts = ""
        for output_name in self.outputs:
            ts += output_name + ":" + self.state.outputs[output_name].touchscreen + ","

        if self.command == 'xrandr':
            return "xrandr " + " ".join(self.configuration.commandlineargs()), ts
        else:
            return "wlr-randr " + " ".join(self.configuration.commandlineargswayfire()), ts

    def check_configuration(self):
        vmax = self.state.virtual.max

        for output_name in self.outputs:
            output_config = self.configuration.outputs[output_name]

            if not output_config.active:
                continue

            # we trust users to know what they are doing
            # (e.g. widget: will accept current mode,
            # but not offer to change it lacking knowledge of alternatives)
            #
            # if output_config.rotation not in output_state.rotations:
            #    raise InadequateConfiguration("Rotation not allowed.")
            # if output_config.mode not in output_state.modes:
            #    raise InadequateConfiguration("Mode not allowed.")

            x = output_config.position[0] + output_config.size[0]
            y = output_config.position[1] + output_config.size[1]

            if x > vmax[0] or y > vmax[1]:
                raise InadequateConfiguration(
                    _("A part of an output is outside the virtual screen."))

            if output_config.position[0] < 0 or output_config.position[1] < 0:
                raise InadequateConfiguration(
                    _("An output is outside the virtual screen."))

    #################### multi-format save functions ####################

    def save_config(self):
        self.check_configuration()
        if self.compositor == 'openbox':
            self._output(*self.configuration.commandlineargs())
            self._write_dispsetup_sh()
        elif self.compositor == "labwc":
            self._output(*self.configuration.commandlineargswayfire())
            self._write_labwc_config (False)
            self._write_labwc_config (True)
            self._write_labwc_touchscreen (False)
            self._write_labwc_touchscreen (True)
            subprocess.run ("labwc --reconfigure", shell=True)
        elif self.compositor == "wayfire":
            if 'NOOP-1' in self.configuration.commandlineargswayfire():
                self._output(*self.configuration.commandlineargswayfire())
            self._write_wayfire_config (False)
            self._write_wayfire_config (True)
        self._load_current_state()

    def _write_dispsetup_sh(self):
        data = "xrandr " + " ".join(self.configuration.commandlineargs())
        file = open ("/tmp/arandr/dispsetup.sh", "w")
        file.write ("#!/bin/sh\nif " + data + " --dryrun ; then \n" + data + "\nfi\n");
        for output_name in self.outputs:
            output_state = self.state.outputs[output_name]
            if output_state.touchscreen != "":
                tscmd = 'xinput --map-to-output "' + output_state.touchscreen + '" ' + output_name
                subprocess.run (tscmd, shell=True)   ## call xinput here, because why not?
                file.write ("if xinput | grep -q \"" + output_state.touchscreen + "\" ; then " + tscmd + " ; fi\n")
        file.write ("if [ -e /usr/share/ovscsetup.sh ] ; then\n. /usr/share/ovscsetup.sh\nfi\nexit 0");
        file.close ()

    def _write_wayfire_config(self, greeter):
        if greeter:
            if os.path.exists ("/usr/share/greeter.ini"):
                inpath = "/usr/share/greeter.ini"
            else:
                inpath = "/etc/wayfire/gtemplate.ini"
            outpath = "/tmp/arandr/greeter.ini"
        else:
            inpath = outpath = os.path.expanduser ('~/.config/wayfire.ini')

        config = configparser.ConfigParser ()
        config.read (inpath)
        tsunused = self.touchscreens.copy()
        for output_name in self.outputs:
            output_config = self.configuration.outputs[output_name]
            output_state = self.state.outputs[output_name]
            section = 'output:' + output_name
            key = output_config.mode.name.replace(' ','@').replace('.','').replace('Hz','')
            config[section] = {}
            if output_config.active:
                config[section]['mode'] = key
                config[section]['position'] = str(int(output_config.position[0])) + ',' + str(int(output_config.position[1]))
                config[section]['transform'] = output_config.rotation.wayname()
            else:
                config[section]['mode'] = "off"
            if output_state.touchscreen != "":
                section = "input-device:" + output_state.touchscreen
                config[section] = {}
                config[section]["output"] = output_name
                tsunused.remove (output_state.touchscreen)
        for tsu in tsunused:
            section = "input-device:" + tsu
            config.remove_section (section)
        with open (outpath, 'w') as configfile:
            config.write (configfile)

    def _write_labwc_config(self, greeter):
        if greeter:
            inpath = "/usr/share/labwc/autostart"
            outpath = "/tmp/arandr/autostart"
        else:
            inpath = outpath = os.path.expanduser ('~/.config/labwc/autostart')
            path = os.path.expanduser ('~/.config/labwc')
            if not os.path.isdir (path):
                os.mkdir (path)

        command = "wlr-randr " + " ".join(self.configuration.commandlineargswayfire()) + " &\n"
        if os.path.isfile (inpath):
            outdata = ''
            found = False
            with open (inpath, "r") as infile:
                for line in infile:
                    if line.find ('wlr-randr') != -1:
                        outdata += command
                        found = True
                    else:
                        outdata += line
                if found is False:
                    if line.strip()[-1] != '&':
                        outdata = outdata.rstrip() + " &\n"
                    elif line[-1] != '\n':
                        outdata += '\n'
                    outdata += command
        else:
            outdata = command
        with open (outpath, "w") as outfile:
            outfile.write(outdata)

    def _write_labwc_touchscreen(self, greeter):
        if greeter:
            inpath = "/usr/share/labwc/rc.xml"
            outpath = '/tmp/arandr/rc.xml'
        else:
            inpath = outpath = os.path.expanduser ('~/.config/labwc/rc.xml')
            path = os.path.expanduser ('~/.config/labwc')
            if not os.path.isdir (path):
                os.mkdir (path)

        xmlet.register_namespace('',"http://openbox.org/3.4/rc")
        if os.path.isfile(inpath):
            tree = xmlet.parse(inpath)
        else:
            newel = xmlet.Element("openbox_config")
            newel.set("xmlns", "http://openbox.org/3.4/rc")
            tree = xmlet.ElementTree(newel)
        root = tree.getroot()
        to_remove = []
        for child in root.iter("{http://openbox.org/3.4/rc}touch"):
            to_remove.append(child)
        for rem in to_remove:
            root.remove(rem)
        for output_name in self.outputs:
            output_state = self.state.outputs[output_name]
            if output_state.touchscreen != "":
                child = xmlet.Element("touch")
                child.set("deviceName", output_state.touchscreen)
                child.set("mapToOutput", output_name)
                root.append(child)
        tree.write(outpath, xml_declaration=True, method="xml", encoding='UTF-8')

    #################### touchscreen mapping ####################

    def _find_touchscreens(self):
       res = subprocess.run ("libinput list-devices | tr \\\\n @ | sed 's/@@/\\\n/g' | grep \"Capabilities:.*touch\" | sed 's/Device:[ \\\t]*//' | cut -d @ -f 1", shell=True, capture_output=True, encoding='utf8')
       self.touchscreens = res.stdout.splitlines()

    def _get_device_touchscreen(self, output_name):
        touchscreen = ""
        if self.compositor == "wayfire":
            config = configparser.ConfigParser ()
            config.read (os.path.expanduser ('~/.config/wayfire.ini'))
            for ts in self.touchscreens:
                section = "input-device:" + ts
                dev = config.get (section, "output", fallback = None)
                if dev == output_name:
                    touchscreen = ts
        elif self.compositor == "labwc":
            rcpath = os.path.expanduser ('~/.config/labwc/rc.xml')
            if os.path.isfile (rcpath):
                tree = xmlet.parse(rcpath)
                root = tree.getroot()
                for child in root.iter("{http://openbox.org/3.4/rc}touch"):
                    if child.get('mapToOutput') == output_name:
                        touchscreen = child.get("deviceName")
        elif self.compositor == "openbox":
            tsfile = None
            if os.path.isfile ("/tmp/arandr/dispsetup.sh"):
                tsfile = open ("/tmp/arandr/dispsetup.sh", "r")
            elif os.path.isfile ("/usr/share/dispsetup.sh"):
                tsfile = open ("/usr/share/dispsetup.sh", "r")
            if tsfile:
                for line in tsfile:
                    if "xinput" in line and output_name in line:
                        touchscreen = line.split('"')[1]
        return touchscreen

    #################### loading from wlr-randr ####################

    def _read_wlr_randr(self):
        output = self._output("")
        totw = 0
        toth = 0
        items = []
        curw = "0"
        curh = "0"
        act = False
        towrite = False
        physical = False
        virtmodes = [
            [['640x480', ''], '640', '480', 'None'],
            [['720x480', ''], '720', '480', 'None'],
            [['800x600', ''], '800', '600', 'None'],
            [['1024x768', ''], '1024', '768', 'None'],
            [['1280x720', ''], '1280', '720', 'None'],
            [['1280x1024', ''], '1280', '1024', 'None'],
            [['1600x1200', ''], '1600', '1200', 'None'],
            [['1920x1080', ''], '1920', '1080', 'None'],
        ]

        for line in output.split('\n'):
            if len (line) > 0 and not line.startswith(' '):
                if towrite:
                    if act:
                        displ[0] = curout + ' connected ' + curw + 'x' + curh + '+' + curx + '+' + cury + ' () ' + curt
                    else:
                        displ[0] = curout + ' connected ()'
                    if physical:
                        displ.append(modes)
                    else:
                        displ.append(virtmodes)
                    items.append(displ)
                towrite = True
                physical = False
                curout = (line.split())[0]
                displ = []
                displ.append (line)
                modes = []
            else:
                res = line.replace (" px, ", " ").replace( "x", " ").split()
                if 'px' in line :
                    if 'current' in line:
                        cur = '*current'
                        curw = res[0]
                        curh = res[1]
                    else:
                        cur = ''
                    if physical:
                        modes.append ([[line.strip().split()[0], cur]])
                        modes[-1].append (res[0])
                        modes[-1].append (res[1])
                        if res[2].replace(".","").isnumeric() :
                            modes[-1].append (' %.3fHz' % float(res[2]))
                        else:
                            modes[-1].append ('None')
                    elif 'current' in line:
                        for mode in virtmodes:
                            if line.strip().split()[0] == mode[0][0]:
                                mode[0][1] = '*current'
                                break
                        else:
                            virtmodes.append ([[line.strip().split()[0], cur]])
                            virtmodes[-1].append (res[0])
                            virtmodes[-1].append (res[1])
                            virtmodes[-1].append ('None')
                elif 'Physical' in line:
                    physical = True
                elif len (res) == 2:
                    if res[0] == 'Position:':
                        pos = res[1].split(',')
                        curx = pos[0]
                        cury = pos[1]
                    elif res[0] == 'Transform:':
                        curt = wlrrot[res[1]]
                        if curt == 'left' or curt == 'right':
                            tmp = curw
                            curw = curh
                            curh = tmp
                        toth += int(curh)
                        totw += int(curw)
                    elif res[0] == 'Enabled:':
                        if res[1] == "no" :
                            act = False
                        else:
                            act = True
        if towrite:
            if act:
                displ[0] = curout + ' connected ' + curw + 'x' + curh + '+' + curx + '+' + cury + ' () ' + curt
            else:
                displ[0] = curout + ' connected ()'
            if physical:
                displ.append(modes)
            else:
                displ.append(virtmodes)
            items.append(displ)
        # create a dummy screenline just for consistency
        if totw > 32767:
            totw = 32767
        if toth > 32767:
            toth = 32767
        screenline = "Screen 0: minimum 16 x 16, current " + str(totw) + " x " + str(toth) + ", maximum 32767 x 32767"
        return screenline, items

    def _load_from_commandlineargswlr(self, commandline):
        args = BetterList(commandline.split(" "))
        if args.pop(0) != 'wlr-randr':
            raise FileSyntaxError()
        # first part is empty, exclude empty parts
        options = dict((a[0], a[1:]) for a in args.split('--output') if a)

        for output_name, output_argument in options.items():
            output = self.configuration.outputs[output_name]
            output_state = self.state.outputs[output_name]
            output.primary = False
            if output_argument == ['--off']:
                output.active = False
            else:
                if len(output_argument) % 2 != 0:
                    raise FileSyntaxError()
                parts = [
                    (output_argument[2 * i], output_argument[2 * i + 1])
                    for i in range(len(output_argument) // 2)
                ]
                for part in parts:
                    if part[0] == '--mode':
                        mode = part[1].replace('@',' ')
                        for namedmode in output_state.modes:
                            if namedmode.name == mode:
                                output.mode = namedmode
                                break
                        else:
                            raise FileLoadError("Not a known mode: %s" % (part[1]))
                    elif part[0] == '--custom-mode':
                        output.mode = NamedSize(Size(part[1]), name=part[1])
                    elif part[0] == '--pos':
                        output.position = Position(part[1].replace(',','x'))
                    elif part[0] == '--transform':
                        if part[1] not in wlrrot:
                            raise FileSyntaxError()
                        output.rotation = Rotation(wlrrot[part[1]])
                    else:
                        raise FileSyntaxError()
                output.active = True

    #################### sub objects ####################

    class State:
        """Represents everything that can not be set by xrandr."""

        virtual = None

        def __init__(self):
            self.outputs = {}

        def __repr__(self):
            return '<%s for %d Outputs, %d connected>' % (
                type(self).__name__, len(self.outputs),
                len([x for x in self.outputs.values() if x.connected])
            )

        class Virtual:
            def __init__(self, min_mode, max_mode):
                self.min = min_mode
                self.max = max_mode

        class Output:
            rotations = None
            connected = None
            touchscreen = None

            def __init__(self, name):
                self.name = name
                self.modes = []

            def __repr__(self):
                return '<%s %r (%d modes)>' % (type(self).__name__, self.name, len(self.modes))

    class Configuration:
        """
        Represents everything that can be set by xrandr
        (and is therefore subject to saving and loading from files)
        """

        virtual = None

        def __init__(self, xrandr):
            self.outputs = {}
            self._xrandr = xrandr

        def __repr__(self):
            return '<%s for %d Outputs, %d active>' % (
                type(self).__name__, len(self.outputs),
                len([x for x in self.outputs.values() if x.active])
            )

        def commandlineargs(self):
            args = []
            for output_name, output in self.outputs.items():
                args.append("--output")
                args.append(output_name)
                if not output.active:
                    args.append("--off")
                else:
                    if Feature.PRIMARY in self._xrandr.features:
                        if output.primary:
                            args.append("--primary")
                    if output.mode.name is None:
                        continue
                    modres=str(output.mode.name).split(" ")
                    args.append("--mode")
                    args.append(str(modres[0]))
                    args.append("--rate")
                    if 'i' in str(modres[0]):
                        freq = 2 * float(str(modres[1]).replace('Hz',''))
                        args.append(str("{:.3f}".format (freq)))
                    else:
                        args.append(str(modres[1]).replace('Hz',''))
                    args.append("--pos")
                    args.append(str(output.position))
                    args.append("--rotate")
                    args.append(output.rotation)
            return args

        def commandlineargswayfire(self):
            args = []
            for output_name, output in self.outputs.items():
                args.append("--output")
                args.append(output_name)
                if not output.active:
                    args.append("--off")
                else:
                    if output.mode.name is None:
                        continue
                    modres=str(output.mode.name).split(" ")
                    if len(modres) > 1:
                        args.append("--mode")
                        args.append(str(modres[0]) + '@' + modres[1])
                    else:
                        args.append("--custom-mode")
                        args.append(str(modres[0]))
                    args.append("--pos")
                    args.append(str(output.position).replace('x',','))
                    args.append("--transform")
                    args.append(output.rotation.wayname())
            return args

        class OutputConfiguration:

            def __init__(self, active, primary, geometry, rotation, modename):
                self.active = active
                self.primary = primary
                if active:
                    self.position = geometry.position
                    self.rotation = rotation
                    if rotation.is_odd:
                        self.mode = NamedSize(
                            Size(reversed(geometry.size)), name=modename)
                    else:
                        self.mode = NamedSize(geometry.size, name=modename)

            size = property(lambda self: NamedSize(
                Size(reversed(self.mode)), name=self.mode.name
            ) if self.rotation.is_odd else self.mode)
