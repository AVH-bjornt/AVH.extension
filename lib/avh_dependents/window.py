# -*- coding: utf-8 -*-
"""The Batch Dependents window.

At the top: how many dependent views to make under each chosen parent
view, and their names built as prefix + field + suffix, where {parent}
stands for each parent's own name. Below: one row per new view with its
parent, its field (editable) and the name it will get, kept up to date
as you type and shown in red with the reason when Revit would refuse it.

Changing the count or the start value renumbers the fields; fields that
are not numbered keep what was typed in them.

Revit and WPF only, so nothing here runs outside Revit and the test
harness puts a stand in for this module. What it shows comes from
`evaluate`, which the script passes in and the harness does test. Built
the same way as the Batch Sheets window (lib/avh_sheets/window.py),
which has run in Revit 2025.
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
from avh_dependents import model                                # noqa: E402

XAML = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Batch Dependents" Width="900" Height="640"
        MinWidth="640" MinHeight="380"
        WindowStartupLocation="CenterScreen" ShowInTaskbar="False"
        ResizeMode="CanResizeWithGrip" FontSize="13">
  <DockPanel Margin="12">
    <Grid DockPanel.Dock="Top" Margin="0,0,0,8">
      <Grid.ColumnDefinitions>
        <ColumnDefinition Width="Auto"/>
        <ColumnDefinition Width="70"/>
        <ColumnDefinition Width="*"/>
      </Grid.ColumnDefinitions>
      <TextBlock Grid.Column="0" Text="How many per view" Margin="0,0,6,0"
                 VerticalAlignment="Center"/>
      <TextBox Grid.Column="1" x:Name="count_box" Padding="3"/>
      <TextBlock Grid.Column="2" x:Name="parents_text" Margin="12,0,0,0"
                 VerticalAlignment="Center" TextWrapping="Wrap"/>
    </Grid>
    <Grid DockPanel.Dock="Top" Margin="0,0,0,4">
      <Grid.ColumnDefinitions>
        <ColumnDefinition Width="Auto"/>
        <ColumnDefinition Width="*"/>
        <ColumnDefinition Width="Auto"/>
        <ColumnDefinition Width="70"/>
        <ColumnDefinition Width="Auto"/>
        <ColumnDefinition Width="*"/>
      </Grid.ColumnDefinitions>
      <TextBlock Grid.Column="0" Text="Name prefix" Margin="0,0,6,0"
                 VerticalAlignment="Center"/>
      <TextBox Grid.Column="1" x:Name="prefix_box" Padding="3"/>
      <TextBlock Grid.Column="2" Text="start at" Margin="12,0,6,0"
                 VerticalAlignment="Center"/>
      <TextBox Grid.Column="3" x:Name="start_box" Padding="3"
               ToolTip="1, 01, 101 or a letter numbers each view's dependents; anything else lets you type each field"/>
      <TextBlock Grid.Column="4" Text="suffix" Margin="12,0,6,0"
                 VerticalAlignment="Center"/>
      <TextBox Grid.Column="5" x:Name="suffix_box" Padding="3"/>
    </Grid>
    <TextBlock DockPanel.Dock="Top" Margin="0,0,0,10" Foreground="#FF555555"
               Text="{}{parent} in the prefix or suffix stands for each parent view's name."/>
    <DockPanel DockPanel.Dock="Bottom" Margin="0,10,0,0">
      <Button x:Name="cancel_button" DockPanel.Dock="Right"
              Content="Cancel" Padding="16,4" Margin="8,0,0,0"/>
      <Button x:Name="create_button" DockPanel.Dock="Right"
              Content="Create dependents" Padding="16,4"/>
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

HEADINGS = (u"Parent view", u"Field", u"New view name")
WIDTHS = ((1.4, GridUnitType.Star), (0.6, GridUnitType.Star),
          (2, GridUnitType.Star))


class BatchDependentsWindow(forms.WPFWindow):
    """`parents`: the parent views' labels. `prefix`, `start`, `suffix`,
    `count`: where to start. `evaluate(prefix, fields_per_parent,
    suffix)` returns model rows, parent by parent."""

    def __init__(self, parents, prefix, start, suffix, count, evaluate):
        forms.WPFWindow.__init__(self, XAML, literal_string=True)
        self.parents = list(parents)
        self.evaluate = evaluate
        self.result = None
        self.field_boxes = [[] for _ in self.parents]
        self.name_blocks = []
        self.rows_ok = False
        self.parents_text.Text = u"{0} parent view(s)".format(
            len(self.parents))
        self.prefix_box.Text = prefix
        self.start_box.Text = start
        self.suffix_box.Text = suffix
        self.count_box.Text = to_text(count)
        self.rebuild()
        for box in (self.prefix_box, self.suffix_box):
            box.TextChanged += self.on_changed
        self.count_box.TextChanged += self.on_rebuild
        self.start_box.TextChanged += self.on_rebuild
        self.create_button.Click += self.on_create
        self.cancel_button.Click += self.on_cancel

    # -- building -------------------------------------------------------

    def _cell(self, row, column, element):
        Grid.SetRow(element, row)
        Grid.SetColumn(element, column)
        self.rows_grid.Children.Add(element)

    def _text(self, text, bold=False):
        block = TextBlock()
        block.Text = text
        block.Margin = Thickness(4, 3, 8, 3)
        block.VerticalAlignment = VerticalAlignment.Center
        block.TextWrapping = TextWrapping.Wrap
        if bold:
            block.FontWeight = FontWeights.Bold
        return block

    def _box(self, text):
        box = TextBox()
        box.Text = text
        box.Margin = Thickness(2, 2, 8, 2)
        box.Padding = Thickness(2)
        box.TextChanged += self.on_changed
        return box

    def _count(self):
        if model.count_problem(self.count_box.Text, len(self.parents)):
            return None
        return int(to_text(self.count_box.Text).strip())

    def rebuild(self):
        """Lay the rows out again for the count, numbered from the start
        value, keeping the fields typed so far that are not numbered."""
        count = self._count()
        if count is None:
            self.rows_ok = False
            self.refresh()
            return
        fields = model.fields(count, to_text(self.start_box.Text),
                              [[to_text(b.Text) for b in own]
                               for own in self.field_boxes])
        grid = self.rows_grid
        for own in self.field_boxes:
            for box in own:
                box.TextChanged -= self.on_changed
        grid.Children.Clear()
        grid.RowDefinitions.Clear()
        grid.ColumnDefinitions.Clear()
        for size, unit in WIDTHS:
            column = ColumnDefinition()
            column.Width = GridLength(size, unit)
            grid.ColumnDefinitions.Add(column)
        for _ in range(count * len(self.parents) + 1):
            row = RowDefinition()
            row.Height = GridLength(1, GridUnitType.Auto)
            grid.RowDefinitions.Add(row)
        for column, heading in enumerate(HEADINGS):
            self._cell(0, column, self._text(heading, bold=True))
        self.field_boxes = []
        self.name_blocks = []
        line = 1
        for parent, own in zip(self.parents, fields):
            boxes = []
            for index, field in enumerate(own):
                if index == 0:
                    self._cell(line, 0, self._text(parent))
                box = self._box(field)
                block = self._text(u"")
                self._cell(line, 1, box)
                self._cell(line, 2, block)
                boxes.append(box)
                self.name_blocks.append(block)
                line += 1
            self.field_boxes.append(boxes)
        self.rows_ok = True
        self.refresh()

    # -- reading and showing --------------------------------------------

    def values(self):
        return (to_text(self.prefix_box.Text),
                [[to_text(b.Text) for b in own] for own in self.field_boxes],
                to_text(self.suffix_box.Text))

    def refresh(self):
        count_problem = model.count_problem(self.count_box.Text,
                                            len(self.parents))
        if count_problem or not self.rows_ok:
            self.status_text.Text = u"How many: " + (
                count_problem or u"type a number")
            self.create_button.IsEnabled = False
            return
        rows = self.evaluate(*self.values())
        problems = 0
        for row, block in zip(rows, self.name_blocks):
            block.Text = row.name
            block.Foreground = Brushes.Black
            if row.problem:
                problems += 1
                block.Foreground = Brushes.Firebrick
                block.Text = u"{0}   ({1})".format(row.name, row.problem)
        note = u""
        if not model.counts_from(to_text(self.start_box.Text)):
            note = (u" Start at 1, 01, 101 or a single letter to number "
                    u"the fields.")
        if problems:
            self.status_text.Text = (u"{0} view(s) cannot be made, shown in "
                                     u"red.".format(problems) + note)
        else:
            self.status_text.Text = (u"{0} dependent view(s) will be "
                                     u"made.".format(len(rows)) + note)
        self.create_button.IsEnabled = bool(rows) and not problems

    # -- events ---------------------------------------------------------

    def on_changed(self, sender, args):
        self.refresh()

    def on_rebuild(self, sender, args):
        self.rebuild()

    def on_create(self, sender, args):
        self.result = self.values()
        self.Close()

    def on_cancel(self, sender, args):
        self.result = None
        self.Close()


def ask(parents, prefix, start, suffix, count, evaluate):
    """(prefix, fields_per_parent, suffix) as left when Create was
    pressed, or None."""
    window = BatchDependentsWindow(parents, prefix, start, suffix, count,
                                   evaluate)
    window.ShowDialog()
    return window.result
