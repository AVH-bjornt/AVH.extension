# -*- coding: utf-8 -*-
"""The Rename window: prefix and suffix boxes, one field box per chosen
view or sheet, and the new name beside each, kept up to date as you
type.

Revit and WPF only, so nothing here runs outside Revit and the test
harness puts a stand in for this module. Everything it decides comes
from `evaluate`, which the script passes in and the harness does test.

Unverified until run in Revit: building rows in code on a pyRevit
`forms.WPFWindow` loaded from a XAML string, and wiring `TextChanged`
and `Click` to Python methods with `+=`. Both are common in pyRevit
tools; neither has been run in this extension before.
"""

import clr

clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from System.Windows import GridLength, GridUnitType, Thickness  # noqa: E402
from System.Windows import FontWeights, TextWrapping            # noqa: E402
from System.Windows import VerticalAlignment                    # noqa: E402
from System.Windows.Controls import (                           # noqa: E402
    ColumnDefinition, RowDefinition, TextBlock, TextBox, Grid)
from System.Windows.Media import Brushes                        # noqa: E402

from pyrevit import forms                                       # noqa: E402

from avh_schedules.compat import to_text                        # noqa: E402

XAML = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Rename Views and Sheets" Width="980" Height="640"
        MinWidth="640" MinHeight="360"
        WindowStartupLocation="CenterScreen" ShowInTaskbar="False"
        ResizeMode="CanResizeWithGrip" FontSize="13">
  <DockPanel Margin="12">
    <Grid DockPanel.Dock="Top" Margin="0,0,0,8">
      <Grid.ColumnDefinitions>
        <ColumnDefinition Width="Auto"/>
        <ColumnDefinition Width="*"/>
        <ColumnDefinition Width="Auto"/>
        <ColumnDefinition Width="*"/>
      </Grid.ColumnDefinitions>
      <TextBlock Grid.Column="0" Text="Prefix" Margin="0,0,6,0"
                 VerticalAlignment="Center"/>
      <TextBox Grid.Column="1" x:Name="prefix_box" Padding="3"/>
      <TextBlock Grid.Column="2" Text="Suffix" Margin="16,0,6,0"
                 VerticalAlignment="Center"/>
      <TextBox Grid.Column="3" x:Name="suffix_box" Padding="3"/>
    </Grid>
    <StackPanel DockPanel.Dock="Top" Orientation="Horizontal"
                Margin="0,0,0,10">
      <TextBlock Text="Number the fields from" VerticalAlignment="Center"
                 Margin="0,0,6,0"/>
      <TextBox x:Name="number_box" Width="60" Padding="3" Text="01"/>
      <Button x:Name="number_button" Content="Number" Margin="6,0,0,0"
              Padding="10,2"/>
      <Button x:Name="reset_button" Content="Put fields back"
              Margin="16,0,0,0" Padding="10,2"/>
    </StackPanel>
    <DockPanel DockPanel.Dock="Bottom" Margin="0,10,0,0">
      <Button x:Name="cancel_button" DockPanel.Dock="Right"
              Content="Cancel" Padding="16,4" Margin="8,0,0,0"
              IsCancel="True"/>
      <Button x:Name="rename_button" DockPanel.Dock="Right"
              Content="Rename" Padding="16,4" IsDefault="True"/>
      <TextBlock x:Name="status_text" VerticalAlignment="Center"
                 TextWrapping="Wrap"/>
    </DockPanel>
    <Border BorderBrush="#FFBBBBBB" BorderThickness="1">
      <ScrollViewer VerticalScrollBarVisibility="Auto">
        <Grid x:Name="rows_grid" Margin="6"/>
      </ScrollViewer>
    </Border>
  </DockPanel>
</Window>
"""

HEADINGS = (u"", u"Current name", u"Field", u"New name")
WIDTHS = ((0, GridUnitType.Auto), (1, GridUnitType.Star),
          (1, GridUnitType.Star), (1.4, GridUnitType.Star))


class RenameWindow(forms.WPFWindow):
    """`labels`: the kind of each item ("Sheet A-101", "Floor Plan").
    `olds`: current names. `fields`: the starting field of each.
    `evaluate(prefix, suffix, fields)` returns model rows."""

    def __init__(self, labels, olds, prefix, suffix, fields, evaluate):
        forms.WPFWindow.__init__(self, XAML, literal_string=True)
        self.olds = list(olds)
        self.start_fields = list(fields)
        self.evaluate = evaluate
        self.result = None
        self.field_boxes = []
        self.new_blocks = []
        self._build(labels)
        self.prefix_box.Text = prefix
        self.suffix_box.Text = suffix
        self.prefix_box.TextChanged += self.on_changed
        self.suffix_box.TextChanged += self.on_changed
        self.number_button.Click += self.on_number
        self.reset_button.Click += self.on_reset
        self.rename_button.Click += self.on_rename
        self.cancel_button.Click += self.on_cancel
        self.refresh()

    # -- building -------------------------------------------------------

    def _cell(self, grid, row, column, element):
        Grid.SetRow(element, row)
        Grid.SetColumn(element, column)
        grid.Children.Add(element)

    def _text(self, text, bold=False):
        block = TextBlock()
        block.Text = text
        block.Margin = Thickness(4, 3, 8, 3)
        block.VerticalAlignment = VerticalAlignment.Center
        block.TextWrapping = TextWrapping.Wrap
        if bold:
            block.FontWeight = FontWeights.Bold
        return block

    def _build(self, labels):
        grid = self.rows_grid
        for size, unit in WIDTHS:
            column = ColumnDefinition()
            column.Width = GridLength(size, unit)
            grid.ColumnDefinitions.Add(column)
        for _ in range(len(self.olds) + 1):
            row = RowDefinition()
            row.Height = GridLength(1, GridUnitType.Auto)
            grid.RowDefinitions.Add(row)
        for column, heading in enumerate(HEADINGS):
            self._cell(grid, 0, column, self._text(heading, bold=True))
        for index, old in enumerate(self.olds):
            row = index + 1
            kind = self._text(labels[index])
            kind.Foreground = Brushes.Gray
            self._cell(grid, row, 0, kind)
            self._cell(grid, row, 1, self._text(old))
            box = TextBox()
            box.Text = self.start_fields[index]
            box.Margin = Thickness(2, 2, 8, 2)
            box.Padding = Thickness(2)
            box.TextChanged += self.on_changed
            self._cell(grid, row, 2, box)
            self.field_boxes.append(box)
            new = self._text(u"")
            self._cell(grid, row, 3, new)
            self.new_blocks.append(new)

    # -- reading and showing --------------------------------------------

    def fields(self):
        return [to_text(box.Text) for box in self.field_boxes]

    def refresh(self):
        rows = self.evaluate(to_text(self.prefix_box.Text),
                             to_text(self.suffix_box.Text), self.fields())
        changing = 0
        problems = 0
        for row, block in zip(rows, self.new_blocks):
            if row.problem:
                problems += 1
                block.Text = u"{0}   ({1})".format(row.new, row.problem)
                block.Foreground = Brushes.Firebrick
            else:
                block.Text = row.new
                block.Foreground = Brushes.Black if row.changes \
                    else Brushes.Gray
                if row.changes:
                    changing += 1
        if problems:
            self.status_text.Text = (
                u"{0} name(s) cannot be used, shown in red.".format(problems))
        elif not changing:
            self.status_text.Text = u"No name would change."
        else:
            self.status_text.Text = u"{0} of {1} would be renamed.".format(
                changing, len(rows))
        self.rename_button.IsEnabled = bool(changing) and not problems

    # -- events ---------------------------------------------------------

    def on_changed(self, sender, args):
        self.refresh()

    def on_number(self, sender, args):
        from avh_rename import model
        values = model.numbers(len(self.field_boxes),
                               to_text(self.number_box.Text))
        if values is None:
            self.status_text.Text = (u"Number from a number such as 1, 01 "
                                     u"or 101, or a single letter.")
            return
        for box, value in zip(self.field_boxes, values):
            box.Text = value
        self.refresh()

    def on_reset(self, sender, args):
        for box, value in zip(self.field_boxes, self.start_fields):
            box.Text = value
        self.refresh()

    def on_rename(self, sender, args):
        self.result = (to_text(self.prefix_box.Text),
                       to_text(self.suffix_box.Text), self.fields())
        self.Close()

    def on_cancel(self, sender, args):
        self.result = None
        self.Close()


def ask(labels, olds, prefix, suffix, fields, evaluate):
    """(prefix, suffix, fields) as left when Rename was pressed, or None."""
    window = RenameWindow(labels, olds, prefix, suffix, fields, evaluate)
    window.ShowDialog()
    return window.result
