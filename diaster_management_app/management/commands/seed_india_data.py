"""
Management command to seed India geographic data, risk zones, shelters,
early warnings, and relief resources for DisasterCare.

Run: python manage.py seed_india_data
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta

from diaster_management_app.models import (
    IndianState, IndianDistrict, DisasterRiskZone, EarlyWarning,
    Shelter, ReliefResource, ResourceCategory, MitigationTask,
)


STATES_DATA = [
    # (name, code, lat, lon, is_ut)
    ("Andhra Pradesh", "AP", 15.9129, 79.7400, False),
    ("Arunachal Pradesh", "AR", 28.2180, 94.7278, False),
    ("Assam", "AS", 26.2006, 92.9376, False),
    ("Bihar", "BR", 25.0961, 85.3131, False),
    ("Chhattisgarh", "CG", 21.2787, 81.8661, False),
    ("Goa", "GA", 15.2993, 74.1240, False),
    ("Gujarat", "GJ", 22.2587, 71.1924, False),
    ("Haryana", "HR", 29.0588, 76.0856, False),
    ("Himachal Pradesh", "HP", 31.1048, 77.1734, False),
    ("Jharkhand", "JH", 23.6102, 85.2799, False),
    ("Karnataka", "KA", 15.3173, 75.7139, False),
    ("Kerala", "KL", 10.8505, 76.2711, False),
    ("Madhya Pradesh", "MP", 22.9734, 78.6569, False),
    ("Maharashtra", "MH", 19.7515, 75.7139, False),
    ("Manipur", "MN", 24.6637, 93.9063, False),
    ("Meghalaya", "ML", 25.4670, 91.3662, False),
    ("Mizoram", "MZ", 23.1645, 92.9376, False),
    ("Nagaland", "NL", 26.1584, 94.5624, False),
    ("Odisha", "OD", 20.9517, 85.0985, False),
    ("Punjab", "PB", 31.1471, 75.3412, False),
    ("Rajasthan", "RJ", 27.0238, 74.2179, False),
    ("Sikkim", "SK", 27.5330, 88.5122, False),
    ("Tamil Nadu", "TN", 11.1271, 78.6569, False),
    ("Telangana", "TS", 18.1124, 79.0193, False),
    ("Tripura", "TR", 23.9408, 91.9882, False),
    ("Uttar Pradesh", "UP", 26.8467, 80.9462, False),
    ("Uttarakhand", "UK", 30.0668, 79.0193, False),
    ("West Bengal", "WB", 22.9868, 87.8550, False),
    # Union Territories
    ("Andaman and Nicobar Islands", "AN", 11.7401, 92.6586, True),
    ("Chandigarh", "CH", 30.7333, 76.7794, True),
    ("Dadra & Nagar Haveli and Daman & Diu", "DD", 20.1809, 73.0169, True),
    ("Delhi", "DL", 28.7041, 77.1025, True),
    ("Jammu & Kashmir", "JK", 33.7782, 76.5762, True),
    ("Ladakh", "LA", 34.1526, 77.5771, True),
    ("Lakshadweep", "LD", 10.5667, 72.6417, True),
    ("Puducherry", "PY", 11.9416, 79.8083, True),
]

DISTRICTS_DATA = {
    "Andhra Pradesh": ["Visakhapatnam", "Vijayawada", "Guntur", "Nellore", "Kurnool", "Tirupati", "Rajahmundry", "Kadapa"],
    "Assam": ["Kamrup", "Dibrugarh", "Jorhat", "Silchar", "Guwahati", "Tezpur", "Nagaon", "Bongaigaon"],
    "Bihar": ["Patna", "Gaya", "Muzaffarpur", "Bhagalpur", "Darbhanga", "Ara", "Nalanda", "Vaishali"],
    "Gujarat": ["Ahmedabad", "Surat", "Vadodara", "Rajkot", "Bhavnagar", "Kutch", "Gandhinagar", "Anand"],
    "Himachal Pradesh": ["Shimla", "Mandi", "Kullu", "Kangra", "Hamirpur", "Solan", "Chamba", "Kinnaur"],
    "Jammu & Kashmir": ["Jammu", "Srinagar", "Anantnag", "Baramulla", "Kupwara", "Pulwama", "Udhampur", "Rajouri"],
    "Karnataka": ["Bengaluru Urban", "Mysuru", "Hubli-Dharwad", "Mangaluru", "Belagavi", "Kalaburagi", "Ballari", "Udupi"],
    "Kerala": ["Thiruvananthapuram", "Kochi", "Kozhikode", "Thrissur", "Kollam", "Alappuzha", "Palakkad", "Wayanad"],
    "Madhya Pradesh": ["Bhopal", "Indore", "Jabalpur", "Gwalior", "Ujjain", "Sagar", "Rewa", "Satna"],
    "Maharashtra": ["Mumbai", "Pune", "Nagpur", "Nashik", "Aurangabad", "Kolhapur", "Raigad", "Ratnagiri"],
    "Odisha": ["Bhubaneswar", "Cuttack", "Puri", "Sambalpur", "Rourkela", "Brahmapur", "Balasore", "Kendrapara"],
    "Rajasthan": ["Jaipur", "Jodhpur", "Udaipur", "Bikaner", "Ajmer", "Kota", "Bharatpur", "Barmer"],
    "Tamil Nadu": ["Chennai", "Coimbatore", "Madurai", "Tiruchirappalli", "Salem", "Tirunelveli", "Thoothukudi", "Vellore"],
    "Telangana": ["Hyderabad", "Warangal", "Nizamabad", "Karimnagar", "Khammam", "Nalgonda", "Adilabad", "Mahbubnagar"],
    "Uttar Pradesh": ["Lucknow", "Kanpur", "Agra", "Varanasi", "Meerut", "Allahabad", "Bareilly", "Aligarh"],
    "Uttarakhand": ["Dehradun", "Haridwar", "Nainital", "Almora", "Pithoragarh", "Chamoli", "Rudraprayag", "Tehri Garhwal"],
    "West Bengal": ["Kolkata", "Howrah", "Darjeeling", "Hooghly", "Murshidabad", "Nadia", "North 24 Parganas", "South 24 Parganas"],
    "Delhi": ["Central Delhi", "East Delhi", "New Delhi", "North Delhi", "South Delhi", "West Delhi", "North East Delhi", "South West Delhi"],
}

RISK_ZONES_DATA = [
    # (name, state_code, hazard_type, risk_level, lat, lon, radius_km, pop, desc)
    ("Brahmaputra Flood Plains", "AS", "flood", "very_high", 26.14, 91.74, 80, 5000000, "Severe annual flooding from Brahmaputra and tributaries"),
    ("Odisha Cyclone Coast", "OD", "cyclone", "very_high", 20.29, 85.82, 120, 3000000, "High-frequency cyclone landfall zone on Bay of Bengal coast"),
    ("Himalayan Seismic Zone V", "UK", "earthquake", "very_high", 30.07, 79.02, 200, 1200000, "Seismic Zone V - highest earthquake risk in Uttarakhand"),
    ("Rajasthan Drought Belt", "RJ", "drought", "high", 27.02, 70.90, 300, 8000000, "Chronic drought-prone Thar Desert region"),
    ("Kerala Landslide Belt", "KL", "landslide", "high", 10.52, 76.21, 50, 500000, "Western Ghats steep slopes highly vulnerable to landslides"),
    ("Vidarbha Heatwave Zone", "MH", "heatwave", "high", 20.70, 78.44, 150, 4000000, "Extreme heatwave conditions in Vidarbha belt"),
    ("Nilgiri Forest Fire Zone", "TN", "forest_fire", "high", 11.41, 76.69, 60, 200000, "Dense forests prone to summer fire outbreaks"),
    ("Andaman Tsunami Risk", "AN", "tsunami", "high", 11.74, 92.66, 100, 350000, "Proximity to Andaman-Sumatra fault zone"),
    ("Himalayan Glacial Lake Zone", "HP", "flood", "high", 31.83, 77.19, 80, 400000, "Glacial Lake Outburst Flood (GLOF) risk in Himachal Pradesh"),
    ("Bihar Flood Plains", "BR", "flood", "very_high", 25.37, 85.14, 200, 10000000, "Annual flooding of Ganga-Kosi river system"),
    ("Kutch Seismic Zone", "GJ", "earthquake", "very_high", 23.73, 69.86, 100, 2000000, "High seismicity - Bhuj earthquake region"),
    ("Bengal Delta Cyclone Zone", "WB", "cyclone", "high", 21.89, 88.53, 150, 7000000, "Sundarbans delta cyclone track"),
    ("AP Cyclone Coast", "AP", "cyclone", "very_high", 15.91, 80.44, 100, 4000000, "Andhra coast - frequent cyclone landfalls"),
    ("Jammu Avalanche Zone", "JK", "landslide", "high", 34.27, 74.78, 60, 300000, "High avalanche and landslide risk in J&K mountains"),
    ("Telangana Drought Zone", "TS", "drought", "moderate", 17.38, 78.48, 200, 5000000, "Recurrent drought affecting rainfed agriculture"),
]

SHELTER_DATA = [
    ("Guwahati Flood Relief Centre", "AS", "Kamrup", 26.14, 91.74, 500, "Red Cross"),
    ("Bhubaneswar Cyclone Shelter", "OD", "Bhubaneswar", 20.30, 85.83, 1000, "ODRAF"),
    ("Dehradun Emergency Shelter", "UK", "Dehradun", 30.32, 78.03, 300, "NDRF"),
    ("Chennai Flood Shelter", "TN", "Chennai", 13.08, 80.27, 800, "Corporation of Chennai"),
    ("Mumbai Disaster Camp", "MH", "Mumbai", 19.08, 72.88, 1500, "MCGM"),
    ("Kolkata Relief Centre", "WB", "Kolkata", 22.57, 88.36, 600, "WBDMA"),
    ("Hyderabad Emergency Hub", "TS", "Hyderabad", 17.38, 78.48, 700, "TSDMA"),
    ("Patna Flood Camp", "BR", "Patna", 25.60, 85.14, 1200, "BSDMA"),
    ("Jaipur Heat Wave Centre", "RJ", "Jaipur", 26.91, 75.79, 400, "Rajasthan SDMA"),
    ("Shimla Avalanche Camp", "HP", "Shimla", 31.10, 77.17, 200, "HP Police"),
]

RESOURCES_DATA = [
    # (category, name, unit, qty)
    ("Food & Water", "Emergency Food Packets", "packets", 10000),
    ("Food & Water", "Drinking Water (1L bottles)", "units", 50000),
    ("Food & Water", "Water Purification Tablets", "packets", 5000),
    ("Medical", "First Aid Kits", "boxes", 500),
    ("Medical", "ORS Sachets", "packets", 20000),
    ("Medical", "Medicines (General)", "boxes", 300),
    ("Medical", "Blood Pressure Monitors", "units", 100),
    ("Clothing & Shelter", "Emergency Blankets", "units", 2000),
    ("Clothing & Shelter", "Tarpaulin Sheets", "units", 1500),
    ("Clothing & Shelter", "Clothing Sets (Mixed)", "sets", 3000),
    ("Rescue Equipment", "Life Jackets", "units", 500),
    ("Rescue Equipment", "Rope (50m coils)", "units", 200),
    ("Rescue Equipment", "Emergency Torches", "units", 1000),
    ("Rescue Equipment", "Inflatable Boats", "units", 50),
    ("Communication", "Satellite Phones", "units", 20),
    ("Communication", "Walkie-Talkies", "units", 100),
]

WARNING_DATA = [
    {
        "title": "Orange Alert: Heavy Rainfall Warning",
        "disaster_type": "flood",
        "severity": "warning",
        "source": "imd",
        "description": "IMD has issued an orange alert for extremely heavy rainfall expected in the next 48 hours. Rivers may flood.",
        "safety_instructions": "Avoid low-lying areas and river banks. Keep emergency supplies ready. Follow district administration orders for evacuation.",
        "states": ["Kerala", "Karnataka", "Tamil Nadu"],
    },
    {
        "title": "Cyclone Pre-Season Advisory",
        "disaster_type": "cyclone",
        "severity": "advisory",
        "source": "imd",
        "description": "IMD advisory: Bay of Bengal cyclone season active. Coastal communities should stay alert.",
        "safety_instructions": "Fisherfolk should not venture into deep sea. Check official NDMA SACHET app for real-time updates.",
        "states": ["Odisha", "West Bengal", "Andhra Pradesh"],
    },
    {
        "title": "Heatwave Watch: Severe Heat Alert",
        "disaster_type": "heatwave",
        "severity": "watch",
        "source": "imd",
        "description": "Temperature expected to exceed 44°C. Vulnerable populations are at risk of heat stroke.",
        "safety_instructions": "Drink water regularly. Avoid outdoor exposure between 11am-4pm. Use fans/coolers. Check on elderly neighbors.",
        "states": ["Rajasthan", "Madhya Pradesh", "Uttar Pradesh"],
    },
]


class Command(BaseCommand):
    help = "Seed India geographic data, risk zones, shelters, resources, and demo warnings"

    def handle(self, *args, **options):
        self.stdout.write("Seeding Indian states and union territories...")
        state_map = {}
        for name, code, lat, lon, is_ut in STATES_DATA:
            state, _ = IndianState.objects.update_or_create(
                code=code,
                defaults={"name": name, "latitude": lat, "longitude": lon, "is_union_territory": is_ut},
            )
            state_map[name] = state
            state_map[code] = state
        self.stdout.write(f"  {len(state_map)//2} states/UTs seeded.")

        self.stdout.write("Seeding districts...")
        dist_count = 0
        district_map = {}
        for state_name, districts in DISTRICTS_DATA.items():
            state = state_map.get(state_name)
            if not state:
                continue
            for dist_name in districts:
                dist, _ = IndianDistrict.objects.update_or_create(
                    state=state,
                    name=dist_name,
                    defaults={},
                )
                district_map[(state_name, dist_name)] = dist
                dist_count += 1
        self.stdout.write(f"  {dist_count} districts seeded.")

        self.stdout.write("Seeding disaster risk zones...")
        for name, state_code, hazard, risk, lat, lon, radius, pop, desc in RISK_ZONES_DATA:
            state = state_map.get(state_code)
            DisasterRiskZone.objects.update_or_create(
                name=name,
                defaults={
                    "state": state,
                    "hazard_type": hazard,
                    "risk_level": risk,
                    "risk_score": {"low": 25, "moderate": 50, "high": 70, "very_high": 90}[risk],
                    "latitude": lat,
                    "longitude": lon,
                    "radius_km": radius,
                    "population_at_risk": pop,
                    "description": desc,
                    "is_active": True,
                },
            )
        self.stdout.write(f"  {len(RISK_ZONES_DATA)} risk zones seeded.")

        self.stdout.write("Seeding shelters...")
        for s_name, state_code, dist_name, lat, lon, cap, mgr in SHELTER_DATA:
            state = state_map.get(state_code)
            dist = district_map.get(({"AS": "Assam", "OD": "Odisha", "UK": "Uttarakhand", "TN": "Tamil Nadu", "MH": "Maharashtra", "WB": "West Bengal", "TS": "Telangana", "BR": "Bihar", "RJ": "Rajasthan", "HP": "Himachal Pradesh"}.get(state_code, ""), dist_name))
            Shelter.objects.update_or_create(
                name=s_name,
                defaults={
                    "state": state,
                    "district": dist,
                    "address": f"{dist_name}, {state_code}",
                    "latitude": lat,
                    "longitude": lon,
                    "capacity": cap,
                    "current_occupancy": 0,
                    "status": Shelter.Status.STANDBY,
                    "has_food": True,
                    "has_water": True,
                    "has_electricity": True,
                    "has_sanitation": True,
                    "managed_by": mgr,
                },
            )
        self.stdout.write(f"  {len(SHELTER_DATA)} shelters seeded.")

        self.stdout.write("Seeding resource categories and resources...")
        cat_map = {}
        for cat_name, r_name, unit, qty in RESOURCES_DATA:
            if cat_name not in cat_map:
                cat, _ = ResourceCategory.objects.get_or_create(name=cat_name)
                cat_map[cat_name] = cat
            ReliefResource.objects.update_or_create(
                name=r_name,
                defaults={
                    "category": cat_map[cat_name],
                    "unit": unit,
                    "quantity_available": qty,
                    "quantity_reserved": 0,
                    "minimum_stock": max(50, qty // 20),
                },
            )
        self.stdout.write(f"  {len(RESOURCES_DATA)} resources seeded.")

        self.stdout.write("Seeding demo early warnings...")
        for w in WARNING_DATA:
            warning, created = EarlyWarning.objects.update_or_create(
                title=w["title"],
                defaults={
                    "disaster_type": w["disaster_type"],
                    "severity": w["severity"],
                    "source": w["source"],
                    "description": w["description"],
                    "safety_instructions": w["safety_instructions"],
                    "issued_at": timezone.now() - timedelta(hours=2),
                    "expires_at": timezone.now() + timedelta(days=2),
                    "is_active": True,
                    "is_verified": True,
                },
            )
            for state_name in w.get("states", []):
                state = state_map.get(state_name)
                if state:
                    warning.affected_states.add(state)
        self.stdout.write(f"  {len(WARNING_DATA)} warnings seeded.")

        self.stdout.write(self.style.SUCCESS("\nIndia data seeded successfully. Run the application and explore DisasterCare."))
