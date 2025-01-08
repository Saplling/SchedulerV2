import imgui
import glfw
from imgui.integrations.glfw import GlfwRenderer
import OpenGL.GL as gl
import sys
from datetime import time, timedelta, datetime
import parser
from parser import Course, CourseSection, CourseSession, TimeRange, Schedule
import pickle
from requests import Session
from appdirs import user_data_dir
import os
from dataclasses import dataclass

if hasattr(sys, '_MEIPASS'):  # PyInstaller's temp directory
    os.environ['PATH'] += os.pathsep + sys._MEIPASS

data_dir = user_data_dir("SchedulerV2", False)
os.makedirs(data_dir, exist_ok=True)
PICKLE_PATH = os.path.join(data_dir, "all_courses.pickle")


def impl_glfw_init():
    width, height = 1600, 900
    window_name = "SchedulerV2"

    if not glfw.init():
        print("Could not initialize OpenGL context")
        sys.exit(1)

    glfw.window_hint(glfw.CONTEXT_VERSION_MAJOR, 3)
    glfw.window_hint(glfw.CONTEXT_VERSION_MINOR, 3)
    glfw.window_hint(glfw.OPENGL_PROFILE, glfw.OPENGL_CORE_PROFILE)
    glfw.window_hint(glfw.OPENGL_FORWARD_COMPAT, gl.GL_TRUE)

    window = glfw.create_window(
        int(width), int(height), window_name, None, None)
    glfw.make_context_current(window)

    if not window:
        glfw.terminate()
        print("Could not initialize Window")
        sys.exit(1)

    return window


week_day = {1: "M", 2: "T", 3: "W", 4: "R", 5: "F"}


class FilteredComboList():
    def __init__(self, label, index=0, items=None):
        self.label = label
        self.index = index
        self.items: dict[Course, list[CourseSection]] = items
        self.filter_text = ""
        self.selected_items = {}
        self.set_text_focus = False

    def show(self):
        IO = imgui.get_io()
        if (IO.key_ctrl and IO.keys_down[glfw.KEY_E]) or self.set_text_focus:
            imgui.set_keyboard_focus_here()
            self.set_text_focus = False
        imgui.push_item_width(300)
        changed, self.filter_text = imgui.input_text(f"##{self.label}_filter", self.filter_text, 256)
        if changed:
            self.index = 0
        filtered_items = [item for item in self.items if self.filter_text.lower() in item.name.lower()]
        filtered_item_names = [item.name for item in self.items if self.filter_text.lower() in item.name.lower()]
        clicked, selected_index = imgui.combo(f"##{self.label}", self.index, filtered_item_names)
        imgui.pop_item_width()
        if clicked:
            self.index = selected_index
        if imgui.button(f"Add Course", 200, 20) or imgui.is_key_pressed(glfw.KEY_ENTER):
            selected_course = filtered_items[selected_index]
            self.selected_items[selected_course] = self.items[selected_course]
            self.filter_text = ""
            self.set_text_focus = True

        available_width = imgui.get_content_region_available_width()
        current_width = 0
        for i, selected_item in enumerate(self.selected_items):
            current_width = current_width + imgui.calc_text_size(selected_item.name)[0] + 20
            if current_width > available_width:
                imgui.new_line()
                current_width = 0
            if imgui.button(selected_item.name):
                del self.selected_items[selected_item]
                break
            if i != len(self.selected_items)-1:
                imgui.same_line()
        if imgui.button("Clear Courses", 200, 20):
            self.selected_items = {}

    def getSectionsForEachCourse(self):
        output_list: list[list[CourseSection]] = []
        for course, course_sections in self.selected_items.items():
            output_list.append(course_sections)
        return output_list


class DaysFilter:
    def __init__(self):
        self.is_range = False
        self.selected_days = 4

    def show(self):
        imgui.text("Day Filter: ")
        imgui.same_line()
        imgui.push_item_width(100)
        clicked, selected_index = imgui.combo("##rangeOrExact", int(self.is_range), ["Exactly", "Atleast"])
        if clicked:
            self.is_range = False if selected_index == 0 else True
        imgui.same_line()
        days_range = [str(i) for i in range(1, 6)]
        clicked, selected_index = imgui.combo("##days_selected", self.selected_days-1, days_range)
        if clicked:
            self.selected_days = selected_index + 1
        imgui.pop_item_width()
        imgui.same_line()
        imgui.text("days")


class FilteredSchedules:
    def __init__(self):
        self.included_sections: set[CourseSection] = set()
        self.excluded_sections: set[CourseSection] = set()
        self.filtered_schedules: set[CourseSection] = set()

    @property
    def filters_exist(self) -> bool:
        return (self.included_sections or self.excluded_sections)

    def filter_schedules(self, schedules: dict[Schedule]):
        self.filtered_schedules = set()
        if not self.filters_exist:
            return schedules
        for schedule in schedules:
            if self.excluded_sections.intersection(schedule.course_sections):
                continue
            if self.included_sections.difference(schedule.course_sections):
                continue
            self.filtered_schedules.add(schedule)
        return self.filtered_schedules

    def clear(self):
        self.__init__()


class Tables:
    def __init__(self):
        self.show_tables = False
        self.schedules: dict[Schedule] = {}
        self.filtered_schedules = FilteredSchedules()

        @dataclass
        class TabChange:
            changed: bool = False
            selected_pos: int = 0
        self.tab_change = TabChange()

    def show(self):
        if imgui.is_key_pressed(glfw.KEY_RIGHT):
            self.tab_change.selected_pos = (self.tab_change.selected_pos + 1) % len(self.schedules)  # Wrap around
            self.tab_change.changed = True
        elif imgui.is_key_pressed(glfw.KEY_LEFT):
            self.tab_change.selected_pos = (self.tab_change.selected_pos - 1) % len(self.schedules)  # Wrap around
            self.tab_change.changed = True

        if self.filtered_schedules.filters_exist:
            available_width = imgui.get_content_region_available_width()
            imgui.columns(2, "TabBarWithFilter", border=True)
            imgui.set_column_width(0, available_width*0.8)
            imgui.set_column_width(1, available_width*0.2)

        if imgui.begin_tab_bar("Main"):
            for i, scheule in enumerate(self.filtered_schedules.filter_schedules(self.schedules)):
                tab_flags = 0
                if self.tab_change.changed and i == self.tab_change.selected_pos:
                    tab_flags = imgui.TAB_ITEM_SET_SELECTED
                with imgui.begin_tab_item(f"{i+1}##Schedule {i+1}", flags=tab_flags) as tab:
                    if tab.selected:
                        self.tab_change.selected_pos = i if not self.tab_change.changed else self.tab_change.selected_pos
                        self._display_sched_table(scheule)
            self.tab_change.changed = False
            imgui.end_tab_bar()

        if self.filtered_schedules.filters_exist:
            imgui.next_column()  # Move to the second column

            # **Right Column: Filter Section**
            imgui.text("Filters")
            imgui.separator()
            imgui.text("Here you can see filters and exclusions:")

            available_width = imgui.get_content_region_available_width()
            current_width = 0
            imgui.bullet_text("Exclusions")
            for i, included_item in enumerate(filtered_copy := self.filtered_schedules.excluded_sections.copy()):  # Assuming `self.excluded_items` is a list
                current_width = current_width + imgui.calc_text_size(included_item.section_id)[0] + 20
                if current_width > available_width:
                    imgui.new_line()
                if imgui.button(f"{included_item.section_id}"):
                    self.filtered_schedules.excluded_sections.remove(included_item)
                if i != len(filtered_copy)-1:
                    imgui.same_line()

            current_width = 0
            imgui.bullet_text("Inclusions")
            for i, included_item in enumerate(filtered_copy := self.filtered_schedules.included_sections.copy()):
                current_width = current_width + imgui.calc_text_size(included_item.section_id)[0] + 20
                if current_width > available_width:
                    imgui.new_line()
                if imgui.button(f"{included_item.section_id}"):
                    self.filtered_schedules.included_sections.remove(included_item)
                if i != len(filtered_copy)-1:
                    imgui.same_line()

            imgui.columns(1)  # Reset columns

    def _display_sched_table(self, schedule):
        available_width, available_height = imgui.get_content_region_available()
        if imgui.begin_table(f"schedule", 6, imgui.TABLE_BORDERS | imgui.TABLE_ROW_BACKGROUND, outer_size_height=available_height, outer_size_width=available_width):
            imgui.table_setup_column("Time")
            imgui.table_setup_column("Monday")
            imgui.table_setup_column("Tuesday")
            imgui.table_setup_column("Wednesday")
            imgui.table_setup_column("Thursday")
            imgui.table_setup_column("Friday")
            imgui.table_headers_row()
            for row in range(25):
                imgui.table_next_row()
                for col in range(6):
                    imgui.table_next_column()
                    if col == 0:
                        set_time = (datetime.combine(datetime.today(), time(hour=9)) + timedelta(minutes=30*row)).time()
                        imgui.text(set_time.strftime("%I:%M %p"))
                    else:
                        if (cell_item := self.schedules[schedule][row][col]) is not None:
                            cell_label = f"{cell_item.section_id} {cell_item.course.name}"
                            if not cell_item.seats_left:
                                imgui.push_style_color(imgui.COLOR_TEXT, 1.0, 0.0, 0.0, 1.0)  # Red text
                            imgui.text(cell_label)
                            if not cell_item.seats_left:
                                imgui.pop_style_color()
                            if imgui.begin_popup_context_item(cell_label+f"##{row}{col}"):
                                if imgui.menu_item(f"Include##{row}{col}")[0]:
                                    self.filtered_schedules.included_sections.add(cell_item)
                                if imgui.menu_item(f"Exclude##{row}{col}")[0]:
                                    self.filtered_schedules.excluded_sections.add(cell_item)
                                imgui.end_popup()
            imgui.end_table()

    def _get_course_at_this_time(self, given_schedule: Schedule, given_time: time, day: str):
        for section in given_schedule.course_sections:
            for session in section.sessions:
                on_the_same_day = set(day).intersection(set(session.days))
                is_within_given_time = session.timing.within_range(given_time)
                if on_the_same_day and is_within_given_time:
                    return section

    def generate_table_positions(self):
        for schedule in self.schedules:
            positions = [[None] * 6 for _ in range(25)]
            for row, _ in enumerate(positions):
                for col, _ in enumerate(_):
                    if col == 0:
                        continue
                    current_time = (datetime.combine(datetime.today(), time(hour=9)) + timedelta(minutes=30*row)).time()
                    current_day = week_day[col]
                    positions[row][col] = self._get_course_at_this_time(schedule, current_time, current_day)
            self.schedules[schedule] = positions

    def apply_day_filter(self, is_range, days):
        new_schedules_dict = {}
        if is_range:
            def passes(given_day, target_day): return given_day <= target_day
        else:
            def passes(given_day, target_day): return given_day == target_day
        for schedule in self.schedules:
            if passes(schedule.days, days):
                new_schedules_dict[schedule] = None
        self.schedules = new_schedules_dict


class ImportSection:
    def __init__(self):
        self.departments = ['ACT', 'ADV', 'AIMC', 'APS', 'ARC', 'BCS', 'BIO', 'BSD', 'BSM', 'BUS', 'CMN', 'COM', 'CRI', 'CYS', 'DES', 'DMJ', 'EAP', 'EBU', 'ECO', 'ELC', 'ENG', 'ENT', 'ENV', 'FIN', 'GED', 'HA',
                            'HOM', 'HRM', 'IDF', 'INB', 'IND', 'JRN', 'LNG', 'MATH', 'MBA', 'MCM', 'MEC', 'MGT', 'MIT', 'MKT', 'MTH', 'NET', 'NNS', 'PHYS', 'PLX', 'PRA', 'PSY', 'QBA', 'RTA', 'SCI', 'SCM', 'SHS', 'SOC', 'SPT', 'SWS']
        self.selected_index = 0
        self.session: Session = None

    def show(self):
        _, self.selected_index = imgui.combo("##departments", self.selected_index, self.departments)
        if imgui.button("Import Sections"):
            if self.session is None:
                self.session = parser.get_session()
            return self.update_pickle(self.departments[self.selected_index])

    def update_pickle(self, department: str) -> dict:
        new_course_dict, self.departments = parser.get_all_courses_in_department(self.session, department)
        try:
            with open(PICKLE_PATH, "rb") as file:
                old_course_dict = pickle.load(file)
        except OSError:
            old_course_dict = {}
        with open(PICKLE_PATH, "wb") as file:
            old_course_dict.update(new_course_dict)
            pickle.dump(old_course_dict, file)
        return old_course_dict


try:
    with open(PICKLE_PATH, "rb") as file:
        courses_dict = pickle.load(file)
except OSError:
    courses_dict = {}
course_selector = FilteredComboList("list", 0, courses_dict)
days_filter = DaysFilter()
tables = Tables()
import_section = ImportSection()


def frame_commands():
    imgui.set_next_window_size(630, 370, condition=imgui.ONCE)
    if imgui.begin("Scheduler"):
        # error_popup.show()
        imgui.columns(2, border=False)
        course_selector.show()
        days_filter.show()
        if imgui.button("Generate Tables"):
            tables.filtered_schedules.clear()
            schedules_dict = {schedule: None for schedule in parser.create_all_possible_schedules(course_selector.getSectionsForEachCourse())}
            tables.schedules = schedules_dict
            tables.apply_day_filter(days_filter.is_range, days_filter.selected_days)
            tables.generate_table_positions()
            if tables.schedules:
                tables.show_tables = True
        if tables.show_tables:
            imgui.set_next_window_size(1300, 540, condition=imgui.ONCE)
            _, opened = imgui.begin("##Schedules", closable=True)
            if opened:
                tables.show()
                imgui.end()
            else:
                tables.show_tables = False
                imgui.end()
        imgui.next_column()
        if new_dict := import_section.show():
            course_selector.items = new_dict
        imgui.end()


def render_frame(impl, window, font=None):
    glfw.poll_events()
    impl.process_inputs()
    imgui.new_frame()

    gl.glClearColor(0.1, 0.1, 0.1, 1)
    gl.glClear(gl.GL_COLOR_BUFFER_BIT)

    if font is not None:
        imgui.push_font(font)
    frame_commands()
    if font is not None:
        imgui.pop_font()

    imgui.render()
    impl.render(imgui.get_draw_data())
    glfw.swap_buffers(window)


def main():
    imgui.create_context()
    window = impl_glfw_init()

    impl = GlfwRenderer(window)

    io = imgui.get_io()
    impl.refresh_font_texture()

    while not glfw.window_should_close(window):
        render_frame(impl, window)

    impl.shutdown()
    glfw.terminate()


if __name__ == "__main__":
    main()
