# -*- coding: utf-8 -*-
"""The Rename Types window.

At the top: the name pattern, plain text with {parameter} names. On the
left: every parameter the chosen types have, with the first type's value
as an example; double-click one, or select it and press Insert, to put
{its name} into the pattern where the cursor is. On the right: one row per
type with its family, its name now and the name it will get, kept up to
date as you type. Names that do not change are grey; names that cannot be
used are red, with the reason.

Revit and WPF only, so nothing here runs outside Revit and the test
harness puts a stand in for this module. What it shows comes from
`evaluate`, which the script passes in and the harness does test. Built
like the Renumber window (lib/avh_renumber/window.py); the ListBox and
inserting at the cursor are new here and not yet seen in Revit.
"""

import clr

clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from System.Windows import GridLength, GridUnitType, Thickness  # noqa: E402
from System.Windows import FontWeights, TextWrapping            # noqa: E402
from System.Windows import VerticalAlignment                    # noqa: E402
from System.Windows.Controls import (                           # noqa: E402
    ColumnDefinition, RowDefinition, TextBlock, Grid)
from System.Windows.Media import Brushes                        # noqa: E402

from pyrevit import forms                                       # noqa: E402

from avh_schedules.compat import to_text                        # noqa: E402

XAML = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Rename Types" Width="1050" Height="680"
        MinWidth="720" MinHeight="400"
        WindowStartupLocation="CenterScreen" ShowInTaskbar="False"
        ResizeMode="CanResizeWithGrip" FontSize="13">
  <DockPanel Margin="12">
    <TextBlock DockPanel.Dock="Top" x:Name="heading_text" Margin="0,0,0,8"
               TextWrapping="Wrap" FontWeight="Bold"/>
    <Grid DockPanel.Dock="Top" Margin="0,0,0,10">
      <Grid.ColumnDefinitions>
        <ColumnDefinition Width="Auto"/>
        <ColumnDefinition Width="*"/>
      </Grid.ColumnDefinitions>
      <TextBlock Grid.Column="0" Text="Name" Margin="0,0,6,0"
                 VerticalAlignment="Center"/>
      <TextBox Grid.Column="1" x:Name="pattern_box" Padding="3"
               ToolTip="Text with parameter names in braces, such as {Width}x{Height} {Material}"/>
    </Grid>
    <DockPanel DockPanel.Dock="Bottom" Margin="0,10,0,0">
      <Button x:Name="cancel_button" DockPanel.Dock="Right"
              Content="Cancel" Padding="16,4" Margin="8,0,0,0"/>
      <Button x:Name="write_button" DockPanel.Dock="Right"
              Content="Rename" Padding="16,4"/>
      <TextBlock x:Name="status_text" VerticalAlignment="Center"
                 TextWrapping="Wrap"/>
    </DockPanel>
    <Grid>
      <Grid.ColumnDefinitions>
        <ColumnDefinition Width="300"/>
        <ColumnDefinition Width="*"/>
      </Grid.ColumnDefinitions>
      <DockPanel Grid.Column="0" Margin="0,0,10,0">
        <TextBlock DockPanel.Dock="Top" Margin="0,0,0,4"
                   Text="Parameters (double-click to insert)"/>
        <Button DockPanel.Dock="Bottom" x:Name="insert_button"
                Content="Insert" Padding="16,4" Margin="0,6,0,0"
                HorizontalAlignment="Left"/>
        <ListBox x:Name="parameter_list"/>
      </DockPanel>
      <Border Grid.Column="1" BorderBrush="#FFBBBBBB" BorderThickness="1">
        <ScrollViewer VerticalScrollBarVisibility="Auto">
          <Grid x:Name="rows_grid" Margin="6"/>
        </ScrollViewer>
      </Border>
    </Grid>
  </DockPanel>
</Window>
"""

HEADINGS = (u"Family", u"Now", u"Will be")
WIDTHS = ((1, GridUnitType.Star), (1, GridUnitType.Star),
          (1, GridUnitType.Star))


class RenameTypesWindow(forms.WPFWindow):
    """`heading`: what was chosen. `count`: how many types. `parameters`:
    [(name, label)] for the list. `evaluate(pattern)` returns (rows,
    problem) as model.plan does."""

    def __init__(self, heading, count, parameters, pattern, evaluate):
        forms.WPFWindow.__init__(self, XAML, literal_string=True)
        self.names = [name for name, _label in parameters]
        self.evaluate = evaluate
        self.result = None
        self.heading_text.Text = heading
        for _name, label in parameters:
            self.parameter_list.Items.Add(label)
        self.pattern_box.Text = pattern
        self.cells = []
        self.build(count)
        self.refresh()
        self.pattern_box.TextChanged += self.on_changed
        self.parameter_list.MouseDoubleClick += self.on_insert
        self.insert_button.Click += self.on_insert
        self.write_button.Click += self.on_write
        self.cancel_button.Click += self.on_cancel
        self.pattern_box.Focus()
        self.pattern_box.CaretIndex = len(to_text(pattern))

    def _text(self, text, bold=False):
        block = TextBlock()
        block.Text = text
        block.Margin = Thickness(4, 3, 8, 3)
        block.VerticalAlignment = VerticalAlignment.Center
        block.TextWrapping = TextWrapping.Wrap
        if bold:
            block.FontWeight = FontWeights.Bold
        return block

    def _cell(self, row, column, element):
        Grid.SetRow(element, row)
        Grid.SetColumn(element, column)
        self.rows_grid.Children.Add(element)

    def build(self, count):
        """One line of text blocks per type; refresh fills them, so
        typing never rebuilds the grid."""
        grid = self.rows_grid
        for size, unit in WIDTHS:
            column = ColumnDefinition()
            column.Width = GridLength(size, unit)
            grid.ColumnDefinitions.Add(column)
        for _ in range(count + 1):
            row = RowDefinition()
            row.Height = GridLength(1, GridUnitType.Auto)
            grid.RowDefinitions.Add(row)
        for column, heading in enumerate(HEADINGS):
            self._cell(0, column, self._text(heading, bold=True))
        for line in range(1, count + 1):
            blocks = []
            for column in range(len(HEADINGS)):
                block = self._text(u"")
                self._cell(line, column, block)
                blocks.append(block)
            self.cells.append(blocks)

    def pattern(self):
        return to_text(self.pattern_box.Text)

    def refresh(self):
        rows, problem = self.evaluate(self.pattern())
        if not rows:
            for blocks in self.cells:
                blocks[2].Text = u""
        changing = 0
        for row, blocks in zip(rows, self.cells):
            blocks[0].Text = row.item.family_label
            blocks[1].Text = row.item.current
            if row.problem:
                blocks[2].Text = u"{0}  ({1})".format(row.new or u"-",
                                                      row.problem)
                blocks[2].Foreground = Brushes.Firebrick
            elif row.changes:
                changing += 1
                blocks[2].Text = row.new
                blocks[2].Foreground = Brushes.Black
            else:
                blocks[2].Text = row.new
                blocks[2].Foreground = Brushes.Gray
        if problem:
            self.status_text.Text = problem
            self.status_text.Foreground = Brushes.Firebrick
            self.write_button.IsEnabled = False
            return
        self.status_text.Foreground = Brushes.Black
        if changing:
            self.status_text.Text = (u"{0} of {1} type(s) will be renamed."
                                     .format(changing, len(rows)))
        else:
            self.status_text.Text = u"Every type is already called this."
        self.write_button.IsEnabled = bool(changing)

    def on_changed(self, sender, args):
        self.refresh()

    def on_insert(self, sender, args):
        index = self.parameter_list.SelectedIndex
        if index is None or index < 0 or index >= len(self.names):
            return
        token = u"{" + self.names[index] + u"}"
        text = self.pattern()
        start = self.pattern_box.SelectionStart
        length = self.pattern_box.SelectionLength
        if start is None or start < 0 or start > len(text):
            start, length = len(text), 0
        self.pattern_box.Text = text[:start] + token + text[start + length:]
        self.pattern_box.Focus()
        self.pattern_box.CaretIndex = start + len(token)

    def on_write(self, sender, args):
        self.result = self.pattern()
        self.Close()

    def on_cancel(self, sender, args):
        self.result = None
        self.Close()


def ask(heading, count, parameters, pattern, evaluate):
    """The pattern as left when Rename was pressed, or None."""
    window = RenameTypesWindow(heading, count, parameters, pattern,
                               evaluate)
    window.ShowDialog()
    return window.result
