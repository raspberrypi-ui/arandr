# ARandR -- Another XRandR GUI
# Copyright (C) 2008 -- 2011 chrysn <chrysn@fsfe.org>
# copyright (C) 2019 actionless <actionless.loveless@gmail.com>
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
"""Main GUI for ARandR"""
# pylint: disable=deprecated-method,deprecated-module,wrong-import-order,missing-docstring,wrong-import-position

import os
import optparse
import inspect
import glob

# import os
# os.environ['DISPLAY']=':0.0'

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib

from . import widget
from .xrandr import XRandR
from .i18n import _
from .meta import (
    __version__, TRANSLATORS, COPYRIGHT, PROGRAMNAME, PROGRAMDESCRIPTION,
)


def actioncallback(function):
    """Wrapper around a function that is intended to be used both as a callback
    from a Gtk.Action and as a normal function.

    Functions taking no arguments will never be given any, functions taking one
    argument (callbacks for radio actions) will be given the value of the action
    or just the argument.

    A first argument called 'self' is passed through.
    """
    argnames = inspect.getfullargspec(function)[0]
    if argnames[0] == 'self':
        has_self = True
        argnames.pop(0)
    else:
        has_self = False
    assert len(argnames) in (0, 1)

    def wrapper(*args):
        args_in = list(args)
        args_out = []
        if has_self:
            args_out.append(args_in.pop(0))
        if len(argnames) == len(args_in):  # called directly
            args_out.extend(args_in)
        elif len(argnames) + 1 == len(args_in):
            if argnames:
                args_out.append(args_in[1].props.value)
        else:
            raise TypeError("Arguments don't match")

        return function(*args_out)

    wrapper.__name__ = function.__name__
    wrapper.__doc__ = function.__doc__
    return wrapper


class Application:
    uixml = """
    <ui>
        <menubar name="MenuBar">
            <menu action="File">
                <menuitem action="Quit" />
            </menu>
            <menu action="Configure">
                <menuitem action="Apply" />
                <menuitem action="Revert" />
                <separator />
                <menu action="Outputs" name="Outputs">
                    <menuitem action="OutputsDummy" />
                </menu>
            </menu>
            <menu action="View">
                <menuitem action="Zoom4" />
                <menuitem action="Zoom8" />
                <menuitem action="Zoom16" />
            </menu>
            <menu action="Help">
                <menuitem action="About" />
            </menu>
        </menubar>
        <toolbar name="ToolBar">
            <toolitem action="Apply" />
            <toolitem action="Revert" />
        </toolbar>
    </ui>
    """

    def __init__(self, file=None, randr_display=None, force_version=False):
        self.window = window = Gtk.Window()
        window.props.title = "Screen Layout Editor"
        window.set_icon_name('computer')
        if os.path.isdir ("/tmp/arandr"):
            tmpfiles = glob.glob("/tmp/arandr/*.*")
            for tmpfile in tmpfiles:
                os.remove (tmpfile)
        else:
            os.mkdir ("/tmp/arandr")

        # actions
        actiongroup = Gtk.ActionGroup('default')
        actiongroup.add_actions([
            ("File", None, _("_File")),
            ("New", Gtk.STOCK_NEW, None, None, None, self.do_new),
            ("Open", Gtk.STOCK_OPEN, None, None, None, self.do_open),
            ("SaveAs", Gtk.STOCK_SAVE_AS, None, None, None, self.do_save_as),

            ("Apply", Gtk.STOCK_APPLY, None, '<Control>Return', None, self.do_apply),
            ("Revert", Gtk.STOCK_UNDO, None, None, None, self.do_revert),
            ("LayoutSettings", Gtk.STOCK_PROPERTIES, None,
             '<Alt>Return', None, self.do_open_properties),

            ("Quit", Gtk.STOCK_QUIT, None, None, None, self.close_app),


            ("View", None, _("_View")),

            ("Configure", None, _("_Layout")),
            ("Outputs", None, _("_Screens")),
            ("OutputsDummy", None, _("Dummy")),

            ("Help", None, _("_Help")),
            ("About", Gtk.STOCK_ABOUT, None, None, None, self.about),
        ])
        actiongroup.add_radio_actions([
            ("Zoom4", None, _("1:4"), None, None, 4),
            ("Zoom8", None, _("1:8"), None, None, 8),
            ("Zoom16", None, _("1:16"), None, None, 16),
        ], 8, self.set_zoom)

        window.connect('destroy', self.close_app)

        # uimanager
        self.uimanager = Gtk.UIManager()
        accelgroup = self.uimanager.get_accel_group()
        window.add_accel_group(accelgroup)

        self.uimanager.insert_action_group(actiongroup, 0)

        self.uimanager.add_ui_from_string(self.uixml)

        # widget
        self.widget = widget.ARandRWidget(
            display=randr_display, force_version=force_version,
            window=self.window
        )

        self.widget.connect('changed', self._widget_changed)
        self._widget_changed(self.widget)

        # window layout
        vbox = Gtk.VBox()
        menubar = self.uimanager.get_widget('/MenuBar')
        vbox.pack_start(menubar, expand=False, fill=False, padding=0)

        vbox.add(self.widget)

        bbar = Gtk.ButtonBox ()
        bbar.set_layout (Gtk.ButtonBoxStyle.END)
        bbar.set_spacing (5)
        bbar.set_margin_top (5)
        bbar.set_margin_bottom (5)
        bbar.set_margin_left (5)
        bbar.set_margin_right (5)
        cbutt = Gtk.Button ()
        cbutt.set_label (_("_Close"))
        cbutt.set_use_underline (True)
        cbutt.connect ("clicked", self.close_app)
        bbar.pack_end (cbutt, expand=False, fill=False, padding=0)
        self.rbutt = Gtk.Button()
        self.rbutt.set_label (_("_Undo"))
        self.rbutt.set_use_underline (True)
        self.rbutt.connect ("clicked", self.do_revert)
        bbar.pack_end (self.rbutt, expand=False, fill=False, padding=0)
        abutt = Gtk.Button()
        abutt.set_label (_("_Apply"))
        abutt.set_use_underline (True)
        abutt.connect ("clicked", self.do_apply)
        bbar.pack_end (abutt, expand=False, fill=False, padding=0)
        vbox.pack_start(bbar, expand=False, fill=False, padding=0)

        window.add(vbox)
        window.show_all()

        self.enable_revert (False)

    #################### actions ####################

    @actioncallback
    # don't use directly: state is not pushed back to action group.
    def set_zoom(self, value):
        self.widget.factor = value
        #self.window.resize(1, 1)

    @actioncallback
    def do_open_properties(self):
        dialog = Gtk.Dialog(
            _("Script Properties"), None,
            Gtk.DialogFlags.MODAL, (Gtk.STOCK_CLOSE, Gtk.ResponseType.ACCEPT)
        )
        dialog.set_default_size(300, 400)

        script_editor = Gtk.TextView()
        script_buffer = script_editor.get_buffer()
        script_buffer.set_text("\n".join(self.filetemplate))
        script_editor.props.editable = False

        # wacom_options = Gtk.Label("FIXME")

        notebook = Gtk.Notebook()
        # notebook.append_page(wacom_options, Gtk.Label(_("Wacom options")))
        notebook.append_page(script_editor, Gtk.Label(_("Script")))

        dialog.vbox.pack_start(notebook, expand=False, fill=False, padding=0)  # pylint: disable=no-member
        dialog.show_all()

        dialog.run()
        dialog.destroy()

    def close_resp (self, widget, response_id):
        if response_id == Gtk.ResponseType.YES:
            os.system ('reboot')
        widget.destroy ()
        Gtk.main_quit ()

    def close_app (self, val):
        self.sudo_copy ()
        if self.widget._xrandr.compositor == 'wayfire'and self.widget.tsreboot and self.rbutt.get_sensitive ():
            self.conf = Gtk.MessageDialog (self.window, Gtk.DialogFlags.MODAL, Gtk.MessageType.INFO, Gtk.ButtonsType.YES_NO, _("Changes to touchscreen will take effect on reboot.\nClick 'Yes' to reboot now, or 'No' to reboot later."))
            self.conf.connect ("response", self.close_resp)
            self.conf.run ()
        else:
            Gtk.main_quit ()

    def sudo_copy (self):
        ag = self.uimanager.get_action_groups()
        rev = ag[0].get_action ("Revert")
        if rev.get_sensitive () :
            if self.widget._xrandr.compositor == 'labwc':
                os.system ("env SUDO_ASKPASS=/usr/lib/arandr/pwdarandr.sh sudo -A mkdir -p /usr/share/labwc/")
                os.system ("env SUDO_ASKPASS=/usr/lib/arandr/pwdarandr.sh sudo -A cp /tmp/arandr/* /usr/share/labwc/")
            else:
                os.system ("env SUDO_ASKPASS=/usr/lib/arandr/pwdarandr.sh sudo -A cp /tmp/arandr/*.* /usr/share/")

    def enable_revert (self, state):
        ag = self.uimanager.get_action_groups()
        rev = ag[0].get_action ("Revert")
        rev.set_sensitive (state)
        self.rbutt.set_sensitive (state)

    def revert_timeout (self):
        self.do_revert ()
        self.conf.destroy ()

    def conf_response (self, widget, response_id):
        if response_id == Gtk.ResponseType.CANCEL or response_id == Gtk.ResponseType.DELETE_EVENT:
            self.do_revert ()
        GLib.source_remove (self.revert_timer)
        widget.destroy ()

    def show_confirm (self):
        self.conf = Gtk.MessageDialog (self.window, Gtk.DialogFlags.MODAL, Gtk.MessageType.INFO, Gtk.ButtonsType.OK_CANCEL, _("Screen updated. Click 'OK' if is this is correct, or 'Cancel' to revert to previous setting. Reverting in 10 seconds..."))
        self.revert_timer = GLib.timeout_add (10000, self.revert_timeout)
        self.conf.connect ("response", self.conf_response)
        self.conf.run ()

    @actioncallback
    def do_apply(self):
        if self.widget.abort_if_unsafe():
            return

        try:
            if self.widget.save():
                self.enable_revert (True)
                self.show_confirm()

        except Exception as exc:  # pylint: disable=broad-except
            dialog = Gtk.MessageDialog(
                None, Gtk.DialogFlags.MODAL, Gtk.MessageType.ERROR,
                Gtk.ButtonsType.OK, _("XRandR failed:\n%s") % exc
            )
            dialog.run()
            dialog.destroy()

    @actioncallback
    def do_revert(self):
        if self.widget.abort_if_unsafe():
            return

        try:
            self.widget.revert ()
            self.enable_revert (False)
        except Exception as exc:  # pylint: disable=broad-except
            dialog = Gtk.MessageDialog(
                None, Gtk.DialogFlags.MODAL, Gtk.MessageType.ERROR,
                Gtk.ButtonsType.OK, _("XRandR failed:\n%s") % exc
            )
            dialog.run()
            dialog.destroy()

    @actioncallback
    def do_new(self):
        self.filetemplate = self.widget.load_from_x()

    @actioncallback
    def do_open(self):
        dialog = self._new_file_dialog(
            _("Open Layout"), Gtk.FileChooserAction.OPEN, Gtk.STOCK_OPEN
        )

        result = dialog.run()
        filenames = dialog.get_filenames()
        dialog.destroy()
        if result == Gtk.ResponseType.ACCEPT:
            assert len(filenames) == 1
            filename = filenames[0]
            self.filetemplate = self.widget.load_from_file(filename)

    @actioncallback
    def do_save_as(self):
        dialog = self._new_file_dialog(
            _("Save Layout"), Gtk.FileChooserAction.SAVE, Gtk.STOCK_SAVE
        )
        dialog.props.do_overwrite_confirmation = True

        result = dialog.run()
        filenames = dialog.get_filenames()
        dialog.destroy()
        if result == Gtk.ResponseType.ACCEPT:
            assert len(filenames) == 1
            filename = filenames[0]
            if not filename.endswith('.sh'):
                filename = filename + '.sh'
            self.widget.save_to_file(filename, self.filetemplate)

    def _new_file_dialog(self, title, dialog_type, buttontype):  # pylint: disable=no-self-use
        dialog = Gtk.FileChooserDialog(title, None, dialog_type)
        dialog.add_button(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL)
        dialog.add_button(buttontype, Gtk.ResponseType.ACCEPT)

        layoutdir = os.path.expanduser('~/.screenlayout/')
        try:
            os.makedirs(layoutdir)
        except OSError:
            pass
        dialog.set_current_folder(layoutdir)

        file_filter = Gtk.FileFilter()
        file_filter.set_name('Shell script (Layout file)')
        file_filter.add_pattern('*.sh')
        dialog.add_filter(file_filter)

        return dialog

    #################### widget maintenance ####################

    def _widget_changed(self, _widget):
        self._populate_outputs()

    def _populate_outputs(self):
        outputs_widget = self.uimanager.get_widget('/MenuBar/Configure/Outputs')
        outputs_widget.props.submenu = self.widget.contextmenu()

    #################### application related ####################

    def about(self, *_args):  # pylint: disable=no-self-use
        dialog = Gtk.AboutDialog()
        dialog.props.program_name = PROGRAMNAME
        dialog.props.version = __version__
        dialog.props.translator_credits = "\n".join(TRANSLATORS)
        dialog.props.copyright = COPYRIGHT
        dialog.props.comments = PROGRAMDESCRIPTION
        licensetext = open(os.path.join(os.path.dirname(
            __file__), 'data', 'gpl-3.txt')).read()
        dialog.props.license = licensetext.replace(
            '<', u'\u2329 ').replace('>', u' \u232a')
        dialog.props.logo_icon_name = 'video-display'
        dialog.run()
        dialog.destroy()

    def run(self):  # pylint: disable=no-self-use
        Gtk.main()


def main():
    parser = optparse.OptionParser(
        usage="%prog",
        description="Another XRandrR GUI",
        version="%%prog %s" % __version__
    )
    parser.add_option(
        '--randr-display',
        help=(
            'Use D as display for xrandr '
            '(but still show the GUI on the display from the environment; '
            'e.g. `localhost:10.0`)'
        ),
        metavar='D'
    )
    parser.add_option(
        '--force-version',
        help='Even run with untested XRandR versions',
        action='store_true'
    )

    (options, args) = parser.parse_args()
    if len(args) >= 1:
        parser.print_usage()
        exit()

    app = Application(
        randr_display=options.randr_display,
        force_version=options.force_version
    )
    app.run()
