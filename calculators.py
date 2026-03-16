# -*- coding: utf-8 -*-

def calculate_severance(monthly_salary, years_worked):
    """퇴직금 계산 (Thai Labor Protection Act)"""
    if years_worked < 0.33:  # 120일 미만
        return 0, "อายุงานน้อยกว่า 120 วัน ไม่มีสิทธิ์รับค่าชดเชย"
    elif years_worked < 1:
        days, detail = 30, "อายุงาน 120 วัน - 1 ปี = 30 วัน"
    elif years_worked < 3:
        days, detail = 90, "อายุงาน 1-3 ปี = 90 วัน"
    elif years_worked < 6:
        days, detail = 180, "อายุงาน 3-6 ปี = 180 วัน"
    elif years_worked < 10:
        days, detail = 240, "อายุงาน 6-10 ปี = 240 วัน"
    elif years_worked < 20:
        days, detail = 300, "อายุงาน 10-20 ปี = 300 วัน"
    else:
        days, detail = 400, "อายุงาน 20 ปีขึ้นไป = 400 วัน"

    daily_wage = monthly_salary / 30
    return round(daily_wage * days), detail


def calculate_leave(monthly_salary, unused_days):
    """미사용 연차 수당 계산"""
    daily_wage = monthly_salary / 30
    payout = round(daily_wage * unused_days)
    return payout


def calculate_unpaid_wages(daily_wage, unpaid_days):
    """미지급 임금 계산"""
    return round(daily_wage * unpaid_days)