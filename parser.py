from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from pathlib import Path
import requests
from bs4 import BeautifulSoup, element
from dataclasses import dataclass
from datetime import time
from collections import defaultdict
import re
from datetime import time, datetime
import copy
from webdriver_manager.chrome import ChromeDriverManager
import sys


class TimeRange:
    def __init__(self, start: time, end: time):
        self.start: time = start
        self.end: time = end

    def __eq__(self, value):
        if isinstance(value, type(self)):
            return (self.start == value.start and self.end == value.end)
        raise ValueError(f"Can not compare type {type(self)} with type {type(value)}")

    def __hash__(self):
        return hash((self.start, self.end))

    def overlaps(self, other: "TimeRange") -> bool:
        if not isinstance(other, self.__class__):
            raise ValueError("Can only compare with other time ranges")
        return not (self.end <= other.start or other.end <= self.start)

    def within_range(self, time_period: time):
        return self.start <= time_period < self.end

    @classmethod
    def parse_from_string(cls, start_time: str, end_time: str):
        start = datetime.strptime(start_time, "%I:%M:%S %p").time()
        end = datetime.strptime(end_time, "%I:%M:%S %p").time()
        return cls(start, end)

    def __repr__(self):
        return f"'TimeRange({self.start}, {self.end})'"


@dataclass(frozen=True, order=True)
class Course:
    course_id: str
    department: str
    name: str
    credit_hours: int


@dataclass(frozen=True, order=True)
class CourseSession:
    instructor: str
    room: str
    days: str
    timing: TimeRange


@dataclass(frozen=True, order=True)
class CourseSection:
    course: Course
    section_id: str
    sessions: tuple[CourseSession]
    seats_left: int


class Schedule:
    def __init__(self, course_sections: set[CourseSection] = None):
        self.course_sections = course_sections if course_sections is not None else set()
        self.days = 0

    def __contains__(self, item):
        if isinstance(item, CourseSection):
            return item in self.course_sections
        elif isinstance(item, str):
            for course_section in self.course_sections:
                if item == course_section.section_id:
                    return True
            return False
        else:
            raise NotImplementedError(f"Type {type(self)} cannot contain {type(item)}")

    def conflicts(self, other_section: CourseSection):
        for section in self.course_sections:
            for session in section.sessions:
                for other_session in other_section.sessions:
                    shared_days = set(other_session.days).intersection(
                        set(session.days))
                    if shared_days and other_session.timing.overlaps(session.timing):
                        return True
        return False

    def add_section(self, section: CourseSection):
        if self.conflicts(section):
            raise ValueError("Provided section conflicts with Schedule")
        self.course_sections.add(section)

    def calculate_days(self):
        days = set()
        for section in self.course_sections:
            for session in section.sessions:
                days.add(session.days)
        self.days = len(days)


def create_all_possible_schedules(sections: list[list[CourseSection]]) -> list[Schedule]:
    def recursively_find_schedules(sections: list[list[CourseSection]], schedules: list[Schedule], current_schedule: Schedule, index: int = 0):
        if index == len(sections):
            current_schedule.calculate_days()
            schedules.append(copy.deepcopy(current_schedule))
            current_schedule = Schedule()
            return

        sections_of_one_course = sections[index]
        for section in sections_of_one_course:
            if current_schedule.conflicts(section):
                continue
            current_schedule.add_section(section)
            recursively_find_schedules(
                sections, schedules, current_schedule, index+1)
            current_schedule.course_sections.remove(section)
    schedules = []
    initial_schedule: Schedule = Schedule()
    recursively_find_schedules(sections, schedules, initial_schedule)
    return schedules


def get_session() -> requests.Session:
    session = requests.Session()
    # Configure Selenium WebDriver
    chrome_options = Options()
    # chrome_options.add_argument("--headless")  # Optional: run in headless mode
    chromedriver = ChromeDriverManager()
    driver = webdriver.Chrome(service=Service(
        chromedriver.install()), options=chrome_options)

    login_url = "https://cudportal.cud.ac.ae/student/login.asp"
    # Open login page
    driver.get(login_url)

    try:
        WebDriverWait(driver, 300).until_not(EC.url_contains(login_url))
        # Get cookies from the browser
        selenium_cookies = driver.get_cookies()

        # Add cookies to requests.Session
        for cookie in selenium_cookies:
            session.cookies.set(cookie['name'], cookie['value'])
    finally:
        driver.quit()
    session.headers = {
        "Accept": r"text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Encoding": r"gzip, deflate, br, zstd",
        "Accept-Language": r"en-US,en;q=0.9,ar-AE;q=0.8,ar;q=0.7",
        "cache-control": r"max-age=0",
        "priority": r"u=0, i",
        "referer": r"https://cudportal.cud.ac.ae/student/cePortalOffering.asp",
        "sec-ch-ua": r'"Google Chrome";v="137", "Chromium";v="137", "Not/A)Brand";v="24"',
        "sec-ch-ua-mobile": r"?0",
        "sec-ch-ua-platform": r"Windows",
        "sec-fetch-dest": r"document",
        "sec-fetch-mode": r"navigate",
        "sec-fetch-site": r"same-origin",
        "sec-fetch-user": r"?1",
        "upgrade-insecure-requests": "1",
        "user-agent": r'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36'


    }

    return session


def get_all_courses_in_department(session: requests.Session, department: str) -> tuple[dict, list[str]]:
    def get_page(num_pages): return get_page_in_department(
        session, num_pages, department)
    response = get_page(1)
    first_page_soup = BeautifulSoup(response.text, "html.parser")
    departments_list = get_departments(first_page_soup)
    num_of_pages = get_pages_num(first_page_soup)
    soups: list[BeautifulSoup] = [first_page_soup]
    for i in range(2, num_of_pages+1):
        soups.append(BeautifulSoup(get_page(i).text, "html.parser"))
    courses: dict[Course, list[CourseSection]] = defaultdict(list)
    for soup in soups:
        table_element = soup.find(
            "table", class_="Portal_Group_Table topLevel")
        all_course_infos = table_element.find_all("tr", class_="courseInfo")
        for info_index, course_info in enumerate(all_course_infos):
            info_elements = course_info.find_all("td")
            course_full_id = info_elements[0].get_text(strip=True)
            course_id = course_full_id[:course_full_id.find("Lec")]
            course_name = info_elements[1].get_text(strip=True)
            credit_hours = int(info_elements[2].get_text(strip=True))
            course = Course(course_id, re.sub(r"\d+", "", course_id), course_name, credit_hours)
            if course.name in [course.name for course in courses]:  # HACK FIX CAUSE CUD SUCKS ASS AND SOMETIMES HAVE IDENTICAL COURSES WITH DIFFERENT COURSE IDS :DDDDDD
                continue
            nested_tables = table_element.find_all("table", class_="Portal_Group_Table nested", summary=f"{course_name} Schedule")
            if not nested_tables:
                nested_tables = table_element.find_all("table", class_="Portal_Group_Table nested", summary=f"{course_name}  Schedule")
            for i, table in enumerate(nested_tables):
                sessions: list[CourseSession] = []
                for row in table.find_all("tr", class_=""):
                    cells = row.find_all("td")
                    instructor = cells[1].get_text(strip=True)
                    room = cells[2].get_text(strip=True)
                    days = cells[3].get_text(strip=True)
                    start_time = cells[5].get_text(strip=True)
                    end_time = cells[6].get_text(strip=True)
                    time_range = TimeRange.parse_from_string(start_time, end_time)
                    sessions.append(CourseSession(instructor, room, days, time_range))
                course_full_id = all_course_infos[i+info_index].find("td").get_text(strip=True)
                max_seats, taken_seats = int(cells[-2].get_text(strip=True)), int(cells[-1].get_text(strip=True))
                seats_left = max_seats - taken_seats
                courses[course].append(CourseSection(course, course_full_id, tuple(sessions), seats_left))
    return (courses, departments_list)


def get_accessKey(session: requests.Session):
    ...


def get_page_in_department(session: requests.Session, page_num, department) -> requests.Response:
    URL = r"https://cudportal.cud.ac.ae/student/cePortalOffering.asp"
    form_data = {
        "IsPostBack": "True",
        "f_TermCalendarID": "71",  # Example: term identifier
        "f_Days": "",  # Days filter
        "f_TimeFrom": "",  # Time range start
        "f_TimeTo": "",  # Time range end
        "f_Campuses": "",  # Campuses filter
        "f_Departments": department,  # Example: a department code
        "f_Divisions": "",  # Division filter
        "page": page_num,  # Start with page 1
        "TimeFrom": "",
        "TimeTo": "",
        "departmentSelect": department,
        "accessKey": "",
    }
    # response = session.post(URL, data=form_data)
    response = session.post(URL, form_data)
    if response.status_code != 200:
        raise RuntimeError("Couldn't get correct response")
    return response


def get_pages_num(soup: BeautifulSoup) -> int:
    line = soup.find("div", class_="Portal_Grid_Pager").get_text(strip=True)
    i = line.find(": ") + 2
    try:
        num = int(line[i::i+2])
    except Exception as e:
        raise e
    return num


def get_departments(soup: BeautifulSoup) -> list[str]:
    options: list[element.Tag] = soup.find("select", id="idDepartments").find_all("option")
    option_str = [option.get_text(strip=True) for option in options]
    return option_str

# result = get_all_courses_in_department(get_session(), "BCS")
# print("hehe")

# with open("all_courses.pickle", "wb") as file:
#     pickle.dump(result, file)
# with open("test.txt", "w") as file:
#     pprint.pprint(result, file)
# with open("all_courses.pickle", "wb") as file:
#     pickle.dump(get_all_courses_in_department(get_session(), "BCS"), file)

# with open("test.txt", "w") as file:
#     pprint.pprint(get_all_courses_in_department(get_session(), "BCS"), file)

# with open("all_courses.pickle", "rb") as pick:
#     BCS_courses: dict[Course, list[CourseSection]] = pickle.load(pick)

# selected_courses = ["BCS306", "BCS307", "BCS309", "BCS323"]
# selected_sections = []
# for course, sections in BCS_courses.items():
#     if course.course_id in selected_courses:
#         selected_sections.append(sections)
# x = create_all_possible_schedules(selected_sections)
# print("Put a breakpoint here :D")


# sess = get_session()
# print("Put a breakpoint here :D")
