# -*- coding: utf-8 -*-
"""The Batch Sheets window.

At the top: the title block, how many sheets, and the number and name
each built as prefix + field + suffix. Below: one row per sheet with its
number field and name field, editable, and the sheet number and name it
will get, kept up to date as you type and shown in red with the reason
when Revit would refuse it.

The name can be numbered the same way: give the name a start value
(1, 01, 101 or a letter) and the name fields count up from it. Left
empty, the name fields are typed per row. Changing the count or a start
value renumbers the rows; fields that are not numbered keep what was
typed in them.

The sheets can go into a sheet collection: none, one already in the
model, or a new one named here. A new collection's name is checked as
you type like the sheet names are.

Given views (shift-click), the window makes one sheet per view: the
count is fixed, each row shows its view, and each name field starts as
the view's name. A "Place views" choice puts each view in the middle of
its sheet, or aligned to a view on another sheet, picked after Create.

Revit and WPF only, so nothing here runs outside Revit and the test
harness puts a stand in for this module. What it shows comes from
`evaluate`, which the script passes in and the harness does test. Built
the same way as the Rename Views window (lib/avh_rename/window.py),
which has run in Revit 2025.
"""

import clr

clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from System.Windows import GridLength, GridUnitType, Thickness  # noqa: E402
from System.Windows import FontWeights, TextWrapping            # noqa: E402
from System.Windows import VerticalAlignment, Visibility        # noqa: E402
from System.Windows.Controls import (                           # noqa: E402
    ColumnDefinition, RowDefinition, TextBlock, TextBox, Grid)
from System.Windows.Media import Brushes                        # noqa: E402

from pyrevit import forms                                       # noqa: E402

from avh_schedules.compat import to_text                        # noqa: E402
from avh_sheets import model                                    # noqa: E402

XAML = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Batch Sheets" Width="980" Height="680"
        MinWidth="700" MinHeight="400"
        WindowStartupLocation="CenterScreen" ShowInTaskbar="False"
        ResizeMode="CanResizeWithGrip" FontSize="13">
  <DockPanel Margin="12">
    <Grid DockPanel.Dock="Top" Margin="0,0,0,8">
      <Grid.ColumnDefinitions>
        <ColumnDefinition Width="Auto"/>
        <ColumnDefinition Width="*"/>
        <ColumnDefinition Width="Auto"/>
        <ColumnDefinition Width="70"/>
      </Grid.ColumnDefinitions>
      <TextBlock Grid.Column="0" Text="Title block" Margin="0,0,6,0"
                 VerticalAlignment="Center"/>
      <ComboBox Grid.Column="1" x:Name="titleblock_box" Padding="3"/>
      <TextBlock Grid.Column="2" Text="How many" Margin="16,0,6,0"
                 VerticalAlignment="Center"/>
      <TextBox Grid.Column="3" x:Name="count_box" Padding="3"/>
    </Grid>
    <Grid DockPanel.Dock="Top" Margin="0,0,0,8">
      <Grid.ColumnDefinitions>
        <ColumnDefinition Width="110"/>
        <ColumnDefinition Width="*"/>
        <ColumnDefinition Width="Auto"/>
        <ColumnDefinition Width="*"/>
      </Grid.ColumnDefinitions>
      <TextBlock Grid.Column="0" Text="Sheet collection"
                 VerticalAlignment="Center"/>
      <ComboBox Grid.Column="1" x:Name="collection_box" Padding="3"/>
      <TextBlock Grid.Column="2" Text="new collection name"
                 Margin="12,0,6,0" VerticalAlignment="Center"/>
      <TextBox Grid.Column="3" x:Name="new_collection_box" Padding="3"/>
    </Grid>
    <Grid DockPanel.Dock="Top" Margin="0,0,0,8" x:Name="place_row">
      <Grid.ColumnDefinitions>
        <ColumnDefinition Width="110"/>
        <ColumnDefinition Width="*"/>
      </Grid.ColumnDefinitions>
      <TextBlock Grid.Column="0" Text="Place views"
                 VerticalAlignment="Center"/>
      <ComboBox Grid.Column="1" x:Name="place_box" Padding="3"/>
    </Grid>
    <Grid DockPanel.Dock="Top" Margin="0,0,0,6">
      <Grid.ColumnDefinitions>
        <ColumnDefinition Width="110"/>
        <ColumnDefinition Width="*"/>
        <ColumnDefinition Width="Auto"/>
        <ColumnDefinition Width="70"/>
        <ColumnDefinition Width="Auto"/>
        <ColumnDefinition Width="*"/>
      </Grid.ColumnDefinitions>
      <TextBlock Grid.Column="0" Text="Number prefix"
                 VerticalAlignment="Center"/>
      <TextBox Grid.Column="1" x:Name="number_prefix_box" Padding="3"/>
      <TextBlock Grid.Column="2" Text="start at" Margin="12,0,6,0"
                 VerticalAlignment="Center"/>
      <TextBox Grid.Column="3" x:Name="start_box" Padding="3"/>
      <TextBlock Grid.Column="4" Text="suffix" Margin="12,0,6,0"
                 VerticalAlignment="Center"/>
      <TextBox Grid.Column="5" x:Name="number_suffix_box" Padding="3"/>
    </Grid>
    <Grid DockPanel.Dock="Top" Margin="0,0,0,10">
      <Grid.ColumnDefinitions>
        <ColumnDefinition Width="110"/>
        <ColumnDefinition Width="*"/>
        <ColumnDefinition Width="Auto"/>
        <ColumnDefinition Width="70"/>
        <ColumnDefinition Width="Auto"/>
        <ColumnDefinition Width="*"/>
      </Grid.ColumnDefinitions>
      <TextBlock Grid.Column="0" Text="Name prefix"
                 VerticalAlignment="Center"/>
      <TextBox Grid.Column="1" x:Name="name_prefix_box" Padding="3"/>
      <TextBlock Grid.Column="2" Text="start at" Margin="12,0,6,0"
                 VerticalAlignment="Center"/>
      <TextBox Grid.Column="3" x:Name="name_start_box" Padding="3"
               ToolTip="Leave empty to type each name; 1, 01, 101 or a letter numbers them"/>
      <TextBlock Grid.Column="4" Text="suffix" Margin="12,0,6,0"
                 VerticalAlignment="Center"/>
      <TextBox Grid.Column="5" x:Name="name_suffix_box" Padding="3"/>
    </Grid>
    <DockPanel DockPanel.Dock="Bottom" Margin="0,10,0,0">
      <Button x:Name="cancel_button" DockPanel.Dock="Right"
              Content="Cancel" Padding="16,4" Margin="8,0,0,0"/>
      <Button x:Name="create_button" DockPanel.Dock="Right"
              Content="Create sheets" Padding="16,4"/>
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

HEADINGS = (u"Number field", u"Sheet number", u"Name field", u"Sheet name")
WIDTHS = ((0.7, GridUnitType.Star), (1, GridUnitType.Star),
          (1.2, GridUnitType.Star), (1.6, GridUnitType.Star))
VIEW_HEADING = u"View"
VIEW_WIDTH = (1.4, GridUnitType.Star)
DEFAULT_NAME = u"Unnamed"
NO_COLLECTION = u"(none)"
NEW_COLLECTION = u"New sheet collection..."
NO_COLLECTIONS_HERE = u"(this Revit version has no sheet collections)"
PLACE_MIDDLE = u"In the middle of each sheet"
PLACE_ALIGNED = (u"Aligned to a view on another sheet (picked after "
                 u"Create sheets)")


class BatchSheetsWindow(forms.WPFWindow):
    """`titleblocks`: labels for the title block list, `chosen` the index
    to start on. `number`: (prefix, start, suffix) to start from.
    `evaluate(number_prefix, number_fields, number_suffix, name_prefix,
    name_fields, name_suffix)` returns model rows. `collections`: the
    names of the model's sheet collections, or None where this Revit has
    none. `views`: labels of the views to put one per sheet, or None;
    `names`: the name field each row starts with."""

    def __init__(self, titleblocks, chosen, number, count, evaluate,
                 collections=None, views=None, names=None):
        forms.WPFWindow.__init__(self, XAML, literal_string=True)
        self.evaluate = evaluate
        self.views = views
        self.start_names = list(names or [])
        self.collections = collections
        if collections is None:
            self.collection_box.Items.Add(NO_COLLECTIONS_HERE)
            self.collection_box.IsEnabled = False
        else:
            self.collection_box.Items.Add(NO_COLLECTION)
            for name in collections:
                self.collection_box.Items.Add(name)
            self.collection_box.Items.Add(NEW_COLLECTION)
        self.collection_box.SelectedIndex = 0
        self.new_collection_box.IsEnabled = False
        self.result = None
        self.number_boxes = []
        self.name_boxes = []
        self.number_blocks = []
        self.name_blocks = []
        self.rows_ok = False
        for label in titleblocks:
            self.titleblock_box.Items.Add(label)
        self.titleblock_box.SelectedIndex = chosen
        self.number_prefix_box.Text = number[0]
        self.start_box.Text = number[1]
        self.number_suffix_box.Text = number[2]
        self.count_box.Text = to_text(count)
        self.place_box.Items.Add(PLACE_MIDDLE)
        self.place_box.Items.Add(PLACE_ALIGNED)
        self.place_box.SelectedIndex = 0
        if views is None:
            self.place_row.Visibility = Visibility.Collapsed
        else:
            self.count_box.IsEnabled = False
            self.Title = u"Batch Sheets: a sheet for each view"
        self.rebuild()
        for box in (self.number_prefix_box, self.number_suffix_box,
                    self.name_prefix_box, self.name_suffix_box):
            box.TextChanged += self.on_changed
        self.count_box.TextChanged += self.on_count
        self.start_box.TextChanged += self.on_start
        self.name_start_box.TextChanged += self.on_start
        self.collection_box.SelectionChanged += self.on_collection
        self.new_collection_box.TextChanged += self.on_changed
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
        if model.count_problem(self.count_box.Text):
            return None
        return int(to_text(self.count_box.Text).strip())

    def rebuild(self):
        """Lay the rows out again for the count, numbered from the start
        values, keeping the fields typed so far that are not numbered."""
        count = self._count()
        if count is None:
            self.rows_ok = False
            self.refresh()
            return
        numbers = model.fields(count, to_text(self.start_box.Text),
                               [to_text(b.Text) for b in self.number_boxes],
                               u"")
        typed = [to_text(b.Text) for b in self.name_boxes]
        if not self.name_boxes:
            typed = self.start_names
        names = model.fields(count, to_text(self.name_start_box.Text),
                             typed, DEFAULT_NAME)
        headings, widths, first = HEADINGS, WIDTHS, 0
        if self.views is not None:
            headings = (VIEW_HEADING,) + HEADINGS
            widths = (VIEW_WIDTH,) + WIDTHS
            first = 1
        grid = self.rows_grid
        for box in self.number_boxes + self.name_boxes:
            box.TextChanged -= self.on_changed
        grid.Children.Clear()
        grid.RowDefinitions.Clear()
        grid.ColumnDefinitions.Clear()
        for size, unit in widths:
            column = ColumnDefinition()
            column.Width = GridLength(size, unit)
            grid.ColumnDefinitions.Add(column)
        for _ in range(count + 1):
            row = RowDefinition()
            row.Height = GridLength(1, GridUnitType.Auto)
            grid.RowDefinitions.Add(row)
        for column, heading in enumerate(headings):
            self._cell(0, column, self._text(heading, bold=True))
        self.number_boxes, self.name_boxes = [], []
        self.number_blocks, self.name_blocks = [], []
        for index in range(count):
            number_box = self._box(numbers[index])
            name_box = self._box(names[index])
            number_block = self._text(u"")
            name_block = self._text(u"")
            if first:
                self._cell(index + 1, 0, self._text(self.views[index]))
            self._cell(index + 1, first, number_box)
            self._cell(index + 1, first + 1, number_block)
            self._cell(index + 1, first + 2, name_box)
            self._cell(index + 1, first + 3, name_block)
            self.number_boxes.append(number_box)
            self.name_boxes.append(name_box)
            self.number_blocks.append(number_block)
            self.name_blocks.append(name_block)
        self.rows_ok = True
        self.refresh()

    # -- reading and showing --------------------------------------------

    def values(self):
        return (to_text(self.number_prefix_box.Text),
                [to_text(b.Text) for b in self.number_boxes],
                to_text(self.number_suffix_box.Text),
                to_text(self.name_prefix_box.Text),
                [to_text(b.Text) for b in self.name_boxes],
                to_text(self.name_suffix_box.Text))

    def collection(self):
        """None, ("existing", index into `collections`) or ("new", name)."""
        index = self.collection_box.SelectedIndex
        if self.collections is None or index <= 0:
            return None
        if index <= len(self.collections):
            return (u"existing", index - 1)
        return (u"new", to_text(self.new_collection_box.Text).strip())

    def refresh(self):
        count_problem = model.count_problem(self.count_box.Text)
        if count_problem or not self.rows_ok:
            self.status_text.Text = u"How many: " + (
                count_problem or u"type a number")
            self.create_button.IsEnabled = False
            return
        rows = self.evaluate(*self.values())
        problems = 0
        for row, number_block, name_block in zip(
                rows, self.number_blocks, self.name_blocks):
            number_block.Text = row.number
            name_block.Text = row.name
            colour = Brushes.Firebrick if row.problem else Brushes.Black
            number_block.Foreground = colour
            name_block.Foreground = colour
            if row.problem:
                problems += 1
                name_block.Text = u"{0}   ({1})".format(row.name,
                                                       row.problem)
        start_note = u""
        if not model.counts_from(to_text(self.start_box.Text)):
            start_note += (u" Start the number at 1, 01, 101 or a single "
                           u"letter to number the rows.")
        name_start = to_text(self.name_start_box.Text).strip()
        if name_start and not model.counts_from(name_start):
            start_note += (u" Start the name at 1, 01, 101 or a single "
                           u"letter to number it, or leave it empty.")
        if problems:
            self.status_text.Text = (u"{0} sheet(s) cannot be made, shown in "
                                     u"red.".format(problems) + start_note)
        else:
            self.status_text.Text = (u"{0} sheet(s) will be made.".format(
                len(rows)) + start_note)
        choice = self.collection()
        collection_problem = u""
        if choice is not None and choice[0] == u"new":
            collection_problem = model.collection_problem(
                choice[1], self.collections)
        if collection_problem:
            self.status_text.Text = (u"Sheet collection: " +
                                     collection_problem + u". " +
                                     self.status_text.Text)
        self.create_button.IsEnabled = (bool(rows) and not problems and
                                        not collection_problem)

    # -- events ---------------------------------------------------------

    def on_changed(self, sender, args):
        self.refresh()

    def on_count(self, sender, args):
        self.rebuild()

    def on_start(self, sender, args):
        self.rebuild()

    def on_collection(self, sender, args):
        choice = self.collection()
        self.new_collection_box.IsEnabled = (choice is not None and
                                             choice[0] == u"new")
        self.refresh()

    def on_create(self, sender, args):
        self.result = dict(
            titleblock=self.titleblock_box.SelectedIndex,
            values=self.values(),
            collection=self.collection(),
            align=(self.views is not None and
                   self.place_box.SelectedIndex == 1))
        self.Close()

    def on_cancel(self, sender, args):
        self.result = None
        self.Close()


def ask(titleblocks, chosen, number, count, evaluate, collections=None,
        views=None, names=None):
    """{"titleblock": index, "values": (number_prefix, number_fields,
    number_suffix, name_prefix, name_fields, name_suffix), "collection":
    None, ("existing", index) or ("new", name), "align": True to align
    the placed views to a view picked next} as left when Create was
    pressed, or None."""
    window = BatchSheetsWindow(titleblocks, chosen, number, count, evaluate,
                               collections, views, names)
    window.ShowDialog()
    return window.result
