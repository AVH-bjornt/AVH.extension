# -*- coding: utf-8 -*-
"""The settings window: the shape, how much larger than the pipe, the
rounding step, and whether to look in linked models.

Revit and WPF only, so nothing here runs outside Revit and the test
harness puts a stand in for this module. Every value is checked by
`model.parse_settings`, which the harness does test.

Unverified until run in Revit: this XAML on a pyRevit
`forms.WPFWindow`. The same pattern is used by Rename Views.
"""

import clr

clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from pyrevit import forms                                   # noqa: E402

from avh_openings import model                              # noqa: E402
from avh_schedules.compat import to_text                    # noqa: E402

XAML = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Openings around Pipes" Width="440" SizeToContent="Height"
        WindowStartupLocation="CenterScreen" ShowInTaskbar="False"
        ResizeMode="NoResize" FontSize="13">
  <StackPanel Margin="16">
    <TextBlock x:Name="hosts_text" TextWrapping="Wrap" Margin="0,0,0,12"/>

    <TextBlock Text="Shape" FontWeight="Bold" Margin="0,0,0,4"/>
    <StackPanel Orientation="Horizontal" Margin="0,0,0,12">
      <RadioButton x:Name="rect_radio" Content="Rectangular"
                   GroupName="shape" Margin="0,0,20,0"/>
      <RadioButton x:Name="circle_radio" Content="Circular"
                   GroupName="shape"/>
    </StackPanel>

    <TextBlock Text="How much larger than the pipe" FontWeight="Bold"
               Margin="0,0,0,4"/>
    <StackPanel Orientation="Horizontal" Margin="0,0,0,4">
      <TextBox x:Name="amount_box" Width="70" Padding="3"/>
      <ComboBox x:Name="mode_combo" Width="250" Margin="8,0,0,0">
        <ComboBoxItem Content="mm clear on every side"/>
        <ComboBoxItem Content="% larger than the pipe"/>
      </ComboBox>
    </StackPanel>
    <TextBlock Text="Measured from the outside of the pipe, insulation included."
               Foreground="Gray" TextWrapping="Wrap" Margin="0,0,0,12"/>

    <StackPanel Orientation="Horizontal" Margin="0,0,0,12">
      <TextBlock Text="Round the size up to" VerticalAlignment="Center"
                 Margin="0,0,8,0"/>
      <TextBox x:Name="round_box" Width="60" Padding="3"/>
      <TextBlock Text="mm  (0 for none)" VerticalAlignment="Center"
                 Margin="6,0,0,0"/>
    </StackPanel>

    <CheckBox x:Name="links_check"
              Content="Include pipes and conduits in linked models"
              Margin="0,0,0,12"/>

    <TextBlock x:Name="status_text" Foreground="Firebrick"
               TextWrapping="Wrap" Margin="0,0,0,8"/>
    <StackPanel Orientation="Horizontal" HorizontalAlignment="Right">
      <Button x:Name="ok_button" Content="Find crossings" Padding="16,4"
              IsDefault="True"/>
      <Button x:Name="cancel_button" Content="Cancel" Padding="16,4"
              Margin="8,0,0,0" IsCancel="True"/>
    </StackPanel>
  </StackPanel>
</Window>
"""


class SettingsWindow(forms.WPFWindow):
    def __init__(self, settings, hosts_line):
        forms.WPFWindow.__init__(self, XAML, literal_string=True)
        self.result = None
        self.hosts_text.Text = hosts_line
        self.rect_radio.IsChecked = settings.shape != model.CIRCULAR
        self.circle_radio.IsChecked = settings.shape == model.CIRCULAR
        self.mode_combo.SelectedIndex = 1 if settings.mode == model.PERCENT \
            else 0
        self.amount_box.Text = model.format_number(settings.amount)
        self.round_box.Text = model.format_number(settings.round_to)
        self.links_check.IsChecked = settings.links
        self.ok_button.Click += self.on_ok
        self.cancel_button.Click += self.on_cancel

    def on_ok(self, sender, args):
        shape = model.CIRCULAR if self.circle_radio.IsChecked \
            else model.RECTANGULAR
        mode = model.PERCENT if self.mode_combo.SelectedIndex == 1 \
            else model.GAP
        try:
            self.result = model.parse_settings(
                shape, mode, to_text(self.amount_box.Text),
                to_text(self.round_box.Text),
                bool(self.links_check.IsChecked))
        except model.SettingsError as exc:
            self.status_text.Text = to_text(exc)
            return
        self.Close()

    def on_cancel(self, sender, args):
        self.result = None
        self.Close()


def ask(settings, hosts_line):
    """The chosen Settings, or None when cancelled."""
    window = SettingsWindow(settings, hosts_line)
    window.ShowDialog()
    return window.result
