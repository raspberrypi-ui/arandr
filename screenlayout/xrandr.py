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
from functools import reduce

from .auxiliary import (
    BetterList, Size, Position, Geometry, FileLoadError, FileSyntaxError,
    InadequateConfiguration, Rotation, ROTATIONS, NORMAL, NamedSize,
)
from .i18n import _

SHELLSHEBANG = '#!/bin/sh'


class Feature:
    PRIMARY = 1


class XRandR:
    DEFAULTTEMPLATE = [SHELLSHEBANG, '%(xrandr)s']

    configuration = None
    state = None
    command = 'xrandr'

    def __init__(self, display=None, force_version=False, command='xrandr'):
        """Create proxy object and check for xrandr at `display`. Fail with
        untested versions unless `force_version` is True."""
        self.command = command
        self.environ = dict(os.environ)
        if display:
            self.environ['DISPLAY'] = display

        if command == 'xrandr':
            version_output = self._output("--version")
            supported_versions = ["1.2", "1.3", "1.4", "1.5"]
            if not any(x in version_output for x in supported_versions) and not force_version:
                raise Exception("XRandR %s required." %
                            "/".join(supported_versions))

        self.features = set()
        if command == 'xrandr':
            if " 1.2" not in version_output:
                self.features.add(Feature.PRIMARY)

        self.find_touchscreens()

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
            if self.command != 'wlr-randr':
                raise Exception("XRandR returned error code %d: %s" %
                            (status, err))
        if err:
            warnings.warn(
                "XRandR wrote to stderr, but did not report an error (Message was: %r)" % err)
        return ret.decode('utf-8')

    def _run(self, *args):
        self._output(*args)

    #################### loading ####################

    def load_from_string(self, data):
        data = data.replace("%", "%%")
        lines = data.split("\n")
        if lines[-1] == '':
            lines.pop()  # don't create empty last line

        if lines[0] != SHELLSHEBANG:
            raise FileLoadError('Not a shell script.')

        xrandrlines = [i for i, l in enumerate(
            lines) if l.strip().startswith(self.command + ' ')]
        if not xrandrlines:
            raise FileLoadError('No recognized xrandr command in this shell script.')
        if len(xrandrlines) > 1:
            raise FileLoadError('More than one xrandr line in this shell script.')
        self._load_from_commandlineargs(lines[xrandrlines[0]].strip())
        lines[xrandrlines[0]] = '%(xrandr)s'

        return lines

    def remap_rotation(self, rotname):
        if rotname.isnumeric():
            name = ('Left', 'Inverted', 'Right')[int(int(rotname) / 90 - 1)]
        else:
            name = rotname.capitalize()
        return Rotation(name)

    def _load_from_commandlineargs(self, commandline):
        self.load_current_state()

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
                        output.rotation = self.remap_rotation(part[1])
                    else:
                        raise FileSyntaxError()
                output.active = True

    def load_current_state(self):  # FIXME -- use a library
        self.configuration = self.Configuration(self)
        self.state = self.State()

        if self.command == 'wlr-randr':
            currentmode, items = self._load_raw_lines_wayfire()
            self._setup_screen (currentmode)
        else:
            screenline, items = self._load_raw_lines()
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

                # modeid = hsplit[3].strip("()")

                current_rotation = NORMAL
                for rotation in ROTATIONS:
                    if hsplit[4] == rotation.lower():
                        current_rotation = Rotation(rotation)
            else:
                active = False
                geometry = None
                # modeid = None
                current_rotation = None

            output.rotations = set()
            for rotation in ROTATIONS:
                if self.command == 'wlr-randr' or rotation.lower() in headline:
                    output.rotations.add(rotation)

            currentname = None
            curmode = hsplit[2].split('+')[0] + ' ' + hsplit[5]
            for detail, w, h, f in details:
                name, _mode_raw = detail[0:2]
                if self.command == 'wlr-randr':
                    name = name + ' ' + f
                else:
                    name = name + f
                mode_id = _mode_raw.strip("()")
                try:
                    size = Size([int(w), int(h)])
                except ValueError:
                    raise Exception(
                        "Output %s parse error: modename %s modeid %s." % (output.name, name, mode_id)
                    )
                if self.command == 'wlr-randr':
                    if curmode == name:
                        currentname = name
                else:
                    if "*current" in detail:
                        currentname = name
                for x in ["+preferred", "*current"]:
                    if x in detail:
                        detail.remove(x)

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
            touchscreen = ""
            if self.command == 'wlr-randr':
                config = configparser.ConfigParser ()
                config.read (os.path.expanduser ('~/.config/wayfire.ini'))
                for ts in self.touchscreens:
                    section = "input-device:" + ts
                    dev = config.get (section, "output", fallback = None)
                    if dev == output.name:
                        touchscreen = ts
            else:
                if os.path.isfile ("/usr/share/tssetup.sh"):
                    tsfile = open ("/usr/share/tssetup.sh", "r")
                    for line in tsfile:
                        if output.name in line:
                            touchscreen = line.split('"')[1]

            self.state.outputs[output.name] = output
            self.configuration.outputs[output.name] = self.configuration.OutputConfiguration(
                active, primary, geometry, current_rotation, currentname, touchscreen
            )

    def _load_raw_lines_wayfire(self):
        output = self._output("")
        items = []
        curw = "0"
        curh = "0"
        curf = "0"
        for line in output.split('\n'):
            if len (line) > 0 and not line.startswith(' '):
                curout = (line.split())[0]
                displ = []
                displ.append (line)
                modes = []
            else:
                res = line.replace (" px, ", " ").replace( "x", " ").split()
                if 'px' in line :
                    modes.append ([line.strip().split()])
                    modes[-1].append (res[0])
                    modes[-1].append (res[1])
                    modes[-1].append (res[2])
                    if 'current' in line:
                        curw = res[0]
                        curh = res[1]
                        curf = res[2]
                elif len (res) == 2:
                    if res[0] == 'Position:':
                        pos = res[1].split(',')
                        curx = pos[0]
                        cury = pos[1]
                    elif res[0] == 'Transform:':
                        curt = self.remap_rotation (res[1]).lower()
                    elif res[0] == 'Scale:':
                        displ[0] = curout + ' connected ' + curw + 'x' + curh + '+' + curx + '+' + cury + ' () ' + curt + ' ' + curf
                        displ.append(modes)
                        items.append(displ)
        current = str(curw) + 'x' + str(curh) + ' ' + str(curf)
        return current, items

    def find_touchscreens(self):
       res = subprocess.run ("libinput list-devices | tr \\\\n @ | sed 's/@@/\\\n/g' | grep \"Capabilities:     touch\" | sed 's/Device:[ \\\t]*//' | cut -d @ -f 1", shell=True, capture_output=True, encoding='utf8')
       self.touchscreens = res.stdout.splitlines()

    def _setup_screen(self, curmode):
        self.state.virtual = self.state.Virtual(
            min_mode=Size((int(320), int(200))),
            max_mode=Size((int(7680), int(7680)))
        )
        res = (curmode.split())[0].split('x')
        self.configuration.virtual = Size(
            (int(res[0]), int(res[1]))
        )

    def _load_raw_lines(self):
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

    def save_to_shellscript_string(self, template=None, additional=None):
        """
        Return a shellscript that will set the current configuration.
        Output can be parsed by load_from_string.

        You may specify a template, which must contain a %(xrandr)s parameter
        and optionally others, which will be filled from the additional dictionary.
        """
        if not template:
            template = self.DEFAULTTEMPLATE
        template = '\n'.join(template) + '\n'

        data = {
            'xrandr': "xrandr " + " ".join(self.configuration.commandlineargs())
        }
        if additional:
            data.update(additional)

        return template % data

    def save_to_x(self):
        self.check_configuration()
        self._run(*self.configuration.commandlineargs())

    def check_configuration(self):
        vmax = self.state.virtual.max

        for output_name in self.outputs:
            output_config = self.configuration.outputs[output_name]
            # output_state = self.state.outputs[output_name]

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
                    if output.mode.name is not None:
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
                        args.append (output.rotation.xname())
            return args

        class OutputConfiguration:

            def __init__(self, active, primary, geometry, rotation, modename, touchscreen):
                self.active = active
                self.primary = primary
                self.touchscreen = touchscreen
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
