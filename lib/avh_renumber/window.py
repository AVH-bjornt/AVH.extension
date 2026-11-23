# -*- coding: utf-8 -*-
"""The Renumber window.

At the top: prefix, start value, step and suffix, and the order the
numbers go in. Below: one row per element with its place in the order,
what it is, the value it holds now and the value it will get, kept up to
date as you type. Values that do not change are shown grey. "Only elements with no
value" keeps every value already there and numbers the empty ones.

Revit and WPF only, so nothing here runs outside Revit and the test
harness puts a stand in for this module. What it shows comes from
`evaluate`, which the script passes in and the harness does test. Built
the same way as the Batch Dependents window (lib/avh_dependents/
window.py), which has run in Revit 2025; the ComboBox is new here and
not yet seen in Revit.
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
        Title="Renumber" Width="900" Height="640"
        MinWidth="640" MinHeight="380"
        WindowStartupLocation="CenterScreen" ShowInTaskbar="False"
        ResizeMode="CanResizeWithGrip" FontSize="13">
  <DockPanel Margin="12">
    <TextBlock DockPanel.Dock="Top" x:Name="parameter_text" Margin="0,0,0,8"
               TextWrapping="Wrap" FontWeight="Bold"/>
    <Grid DockPanel.Dock="Top" Margin="0,0,0,6">
      <Grid.ColumnDefinitions>
        <ColumnDefinition Width="Auto"/>
        <ColumnDefinition Width="*"/>
        <ColumnDefinition Width="Auto"/>
        <ColumnDefinition Width="70"/>
        <ColumnDefinition Width="Auto"/>
        <ColumnDefinition Width="50"/>
        <ColumnDefinition Width="Auto"/>
        <ColumnDefinition Width="*"/>
      </Grid.ColumnDefinitions>
      <TextBlock Grid.Column="0" Text="Prefix" Margin="0,0,6,0"
                 VerticalAlignment="Center"/>
      <TextBox Grid.Column="1" x:Name="prefix_box" Padding="3"/>
      <TextBlock Grid.Column="2" Text="start at" Margin="12,0,6,0"
                 VerticalAlignment="Center"/>
      <TextBox Grid.Column="3" x:Name="start_box" Padding="3"
               ToolTip="1, 01, 101 or a single letter. 01 keeps two digits: 01, 02 ... 10"/>
      <TextBlock Grid.Column="4" Text="step" Margin="12,0,6,0"
                 VerticalAlignment="Center"/>
      <TextBox Grid.Column="5" x:Name="step_box" Padding="3"/>
      <TextBlock Grid.Column="6" Text="suffix" Margin="12,0,6,0"
                 VerticalAlignment="Center"/>
      <TextBox Grid.Column="7" x:Name="suffix_box" Padding="3"/>
    </Grid>
    <Grid DockPanel.Dock="Top" Margin="0,0,0,10">
      <Grid.ColumnDefinitions>
        <ColumnDefinition Width="Auto"/>
        <ColumnDefinition Width="320"/>
        <ColumnDefinition Width="*"/>
      </Grid.ColumnDefinitions>
      <TextBlock Grid.Column="0" Text="Order" Margin="0,0,6,0"
                 VerticalAlignment="Center"/>
      <ComboBox Grid.Column="1" x:Name="order_box" Padding="3"/>
      <CheckBox Grid.Column="2" x:Name="empty_box" Margin="16,0,0,0"
                VerticalAlignment="Center"
                Content="Only elements with no value"
                ToolTip="Elements that already hold a value keep it; the empty ones are numbered, skipping the numbers already used"/>
    </Grid>
    <DockPanel DockPanel.Dock="Bottom" Margin="0,10,0,0">
      <Button x:Name="cancel_button" DockPanel.Dock="Right"
              Content="Cancel" Padding="16,4" Margin="8,0,0,0"/>
      <Button x:Name="write_button" DockPanel.Dock="Right"
              Content="Write" Padding="16,4"/>
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

HEADINGS = (u"#", u"Element", u"Now", u"Will be")
WIDTHS = ((40, GridUnitType.Pixel), (2, GridUnitType.Star),
          (1, GridUnitType.Star), (1, GridUnitType.Star))


class RenumberWindow(forms.WPFWindow):
    """`heading`: which parameter, shown at the top. `count`: how many
    elements. `orders`: the order choices, the first one selected.
    `evaluate(order, prefix, start, step, suffix, only_empty)` returns
    (rows, problem) as model.plan does."""

    def __init__(self, heading, count, orders, prefix, start, step, suffix,
                 evaluate):
        forms.WPFWindow.__init__(self, XAML, literal_string=True)
        self.orders = list(orders)
        self.evaluate = evaluate
        self.result = None
        self.parameter_text.Text = heading
        self.prefix_box.Text = prefix
        self.start_box.Text = start
        self.step_box.Text = step
        self.suffix_box.Text = suffix
        for order in self.orders:
            self.order_box.Items.Add(order)
        self.order_box.SelectedIndex = 0
        self.cells = []
        self.build(count)
        self.refresh()
        for box in (self.prefix_box, self.start_box, self.step_box,
                    self.suffix_box):
            box.TextChanged += self.on_changed
        self.order_box.SelectionChanged += self.on_changed
        self.empty_box.Checked += self.on_changed
        self.empty_box.Unchecked += self.on_changed
        self.write_button.Click += self.on_write
        self.cancel_button.Click += self.on_cancel

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
        """One line of four text blocks per element; refresh fills them,
        so typing never rebuilds the grid."""
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

    def values(self):
        index = self.order_box.SelectedIndex
        if index is None or index < 0:
            index = 0
        return (self.orders[index], to_text(self.prefix_box.Text),
                to_text(self.start_box.Text), to_text(self.step_box.Text),
                to_text(self.suffix_box.Text),
                bool(self.empty_box.IsChecked))

    def refresh(self):
        rows, problem = self.evaluate(*self.values())
        if problem:
            for blocks in self.cells:
                blocks[3].Text = u""
            self.status_text.Text = problem
            self.status_text.Foreground = Brushes.Firebrick
            self.write_button.IsEnabled = False
            return
        changing = 0
        for number, (row, blocks) in enumerate(zip(rows, self.cells), 1):
            blocks[0].Text = to_text(number)
            blocks[1].Text = row.item.label
            blocks[2].Text = row.item.current or u"-"
            blocks[3].Text = row.new
            if row.changes:
                changing += 1
                blocks[3].Foreground = Brushes.Black
            else:
                blocks[3].Foreground = Brushes.Gray
        self.status_text.Foreground = Brushes.Black
        if changing:
            self.status_text.Text = (u"{0} of {1} value(s) will change."
                                     .format(changing, len(rows)))
        else:
            self.status_text.Text = u"Every value is already this."
        self.write_button.IsEnabled = bool(changing)

    def on_changed(self, sender, args):
        self.refresh()

    def on_write(self, sender, args):
        self.result = self.values()
        self.Close()

    def on_cancel(self, sender, args):
        self.result = None
        self.Close()


def ask(heading, count, orders, prefix, start, step, suffix, evaluate):
    """(order, prefix, start, step, suffix, only_empty) as left when
    Write was pressed, or None."""
    window = RenumberWindow(heading, count, orders, prefix, start, step,
                            suffix, evaluate)
    window.ShowDialog()
    return window.result
