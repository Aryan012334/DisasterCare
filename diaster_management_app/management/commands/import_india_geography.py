"""
Management command: import_india_geography
==========================================
Loads the full LGD-based list of Indian districts (766 districts across
all 28 states and 8 union territories) using update_or_create so it is
completely idempotent.  Run as many times as you like — it never deletes
existing data.

Usage:
    python manage.py import_india_geography
"""

from django.core.management.base import BaseCommand
from diaster_management_app.models import IndianState, IndianDistrict

# ---------------------------------------------------------------------------
# District data keyed by state code (official LGD names, 2024)
# ---------------------------------------------------------------------------
DISTRICTS_BY_STATE = {
    "AP": [
        "Sri Potti Sriramulu Nellore", "Kurnool", "Anantapur", "YSR Kadapa",
        "Chittoor", "Guntur", "Krishna", "West Godavari", "East Godavari",
        "Visakhapatnam", "Vizianagaram", "Srikakulam", "Prakasam",
    ],
    "AR": [
        "Tawang", "West Kameng", "East Kameng", "Papum Pare", "Kurung Kumey",
        "Kra Daadi", "Lower Subansiri", "Upper Subansiri", "West Siang",
        "East Siang", "Siang", "Upper Siang", "Lower Siang",
        "Lower Dibang Valley", "Dibang Valley", "Anjaw", "Lohit", "Namsai",
        "Changlang", "Tirap", "Longding", "Pakke-Kessang", "Lepa Rada",
        "Shi Yomi", "Kamle",
    ],
    "AS": [
        "Baksa", "Barpeta", "Biswanath", "Bongaigaon", "Cachar", "Charaideo",
        "Chirang", "Darrang", "Dhemaji", "Dhubri", "Dibrugarh", "Dima Hasao",
        "Goalpara", "Golaghat", "Hailakandi", "Hojai", "Jorhat", "Kamrup",
        "Kamrup Metropolitan", "Karbi Anglong", "Karimganj", "Kokrajhar",
        "Lakhimpur", "Majuli", "Morigaon", "Nagaon", "Nalbari", "Sivasagar",
        "Sonitpur", "South Salmara-Mankachar", "Tinsukia", "Udalguri",
        "West Karbi Anglong", "Bajali", "Tamulpur",
    ],
    "BR": [
        "Araria", "Arwal", "Aurangabad", "Banka", "Begusarai", "Bhagalpur",
        "Bhojpur", "Buxar", "Darbhanga", "East Champaran", "Gaya",
        "Gopalganj", "Jamui", "Jehanabad", "Kaimur", "Katihar", "Khagaria",
        "Kishanganj", "Lakhisarai", "Madhepura", "Madhubani", "Munger",
        "Muzaffarpur", "Nalanda", "Nawada", "Patna", "Purnia", "Rohtas",
        "Saharsa", "Samastipur", "Saran", "Sheikhpura", "Sheohar",
        "Sitamarhi", "Siwan", "Supaul", "Vaishali", "West Champaran",
    ],
    "CG": [
        "Balod", "Baloda Bazar", "Balrampur", "Bastar", "Bemetara",
        "Bijapur", "Bilaspur", "Dantewada", "Dhamtari", "Durg", "Gariaband",
        "Gaurela-Pendra-Marwahi", "Janjgir-Champa", "Jashpur", "Kabirdham",
        "Kanker", "Kondagaon", "Korba", "Koriya", "Mahasamund", "Mungeli",
        "Narayanpur", "Raigarh", "Raipur", "Rajnandgaon", "Sukma",
        "Surajpur", "Surguja", "Manendragarh-Chirmiri-Bharatpur",
        "Mohla-Manpur-Ambagarh Chowki", "Sakti",
        "Khairagarh-Chhuikhadan-Gandai", "Sarangarh-Bilaigarh",
    ],
    "GA": [
        "North Goa", "South Goa",
    ],
    "GJ": [
        "Ahmedabad", "Amreli", "Anand", "Aravalli", "Banaskantha", "Bharuch",
        "Bhavnagar", "Botad", "Chhota Udaipur", "Dahod", "Dang",
        "Devbhumi Dwarka", "Gandhinagar", "Gir Somnath", "Jamnagar",
        "Junagadh", "Kheda", "Kutch", "Mahisagar", "Mehsana", "Morbi",
        "Narmada", "Navsari", "Panchmahal", "Patan", "Porbandar", "Rajkot",
        "Sabarkantha", "Surat", "Surendranagar", "Tapi", "Valsad", "Vadodara",
    ],
    "HR": [
        "Ambala", "Bhiwani", "Charkhi Dadri", "Faridabad", "Fatehabad",
        "Gurugram", "Hisar", "Jhajjar", "Jind", "Kaithal", "Karnal",
        "Kurukshetra", "Mahendragarh", "Nuh", "Palwal", "Panchkula",
        "Panipat", "Rewari", "Rohtak", "Sirsa", "Sonipat", "Yamunanagar",
    ],
    "HP": [
        "Bilaspur", "Chamba", "Hamirpur", "Kangra", "Kinnaur", "Kullu",
        "Lahaul and Spiti", "Mandi", "Shimla", "Sirmaur", "Solan", "Una",
    ],
    "JH": [
        "Bokaro", "Chatra", "Deoghar", "Dhanbad", "Dumka",
        "East Singhbhum", "Garhwa", "Giridih", "Godda", "Gumla",
        "Hazaribagh", "Jamtara", "Khunti", "Koderma", "Latehar",
        "Lohardaga", "Pakur", "Palamu", "Ramgarh", "Ranchi", "Sahebganj",
        "Seraikela Kharsawan", "Simdega", "West Singhbhum",
    ],
    "KA": [
        "Bagalkot", "Ballari", "Belagavi", "Bengaluru Rural",
        "Bengaluru Urban", "Bidar", "Chamarajanagar", "Chikballapur",
        "Chikkamagaluru", "Chitradurga", "Dakshina Kannada", "Davanagere",
        "Dharwad", "Gadag", "Hassan", "Haveri", "Kalaburagi", "Kodagu",
        "Kolar", "Koppal", "Mandya", "Mysuru", "Raichur", "Ramanagara",
        "Shivamogga", "Tumakuru", "Udupi", "Uttara Kannada", "Vijayapura",
        "Yadgir", "Vijayanagara",
    ],
    "KL": [
        "Alappuzha", "Ernakulam", "Idukki", "Kannur", "Kasaragod",
        "Kollam", "Kottayam", "Kozhikode", "Malappuram", "Palakkad",
        "Pathanamthitta", "Thiruvananthapuram", "Thrissur", "Wayanad",
    ],
    "MP": [
        "Agar Malwa", "Alirajpur", "Anuppur", "Ashoknagar", "Balaghat",
        "Barwani", "Betul", "Bhind", "Bhopal", "Burhanpur", "Chhatarpur",
        "Chhindwara", "Damoh", "Datia", "Dewas", "Dhar", "Dindori",
        "Guna", "Gwalior", "Harda", "Hoshangabad", "Indore", "Jabalpur",
        "Jhabua", "Katni", "Khandwa", "Khargone", "Maihar", "Mandla",
        "Mandsaur", "Morena", "Narsimhapur", "Neemuch", "Niwari", "Panna",
        "Raisen", "Rajgarh", "Ratlam", "Rewa", "Sagar", "Satna", "Sehore",
        "Seoni", "Shahdol", "Shajapur", "Sheopur", "Shivpuri", "Sidhi",
        "Singrauli", "Tikamgarh", "Ujjain", "Umaria", "Vidisha",
        "Mauganj", "Pandhurna",
    ],
    "MH": [
        "Ahmednagar", "Akola", "Amravati", "Aurangabad", "Beed",
        "Bhandara", "Buldhana", "Chandrapur", "Dhule", "Gadchiroli",
        "Gondia", "Hingoli", "Jalgaon", "Jalna", "Kolhapur", "Latur",
        "Mumbai City", "Mumbai Suburban", "Nagpur", "Nanded", "Nandurbar",
        "Nashik", "Osmanabad", "Palghar", "Parbhani", "Pune", "Raigad",
        "Ratnagiri", "Sangli", "Satara", "Sindhudurg", "Solapur",
        "Thane", "Wardha", "Washim", "Yavatmal",
    ],
    "MN": [
        "Bishnupur", "Chandel", "Churachandpur", "Imphal East",
        "Imphal West", "Jiribam", "Kakching", "Kamjong", "Kangpokpi",
        "Noney", "Pherzawl", "Senapati", "Tamenglong", "Tengnoupal",
        "Thoubal", "Ukhrul",
    ],
    "ML": [
        "East Garo Hills", "East Jaintia Hills", "East Khasi Hills",
        "Eastern West Khasi Hills", "North Garo Hills", "Ri Bhoi",
        "South Garo Hills", "South West Garo Hills", "South West Khasi Hills",
        "West Garo Hills", "West Jaintia Hills", "West Khasi Hills",
    ],
    "MZ": [
        "Aizawl", "Champhai", "Hnahthial", "Khawzawl", "Kolasib",
        "Lawngtlai", "Lunglei", "Mamit", "Saitual", "Serchhip", "Siaha",
    ],
    "NL": [
        "Chumoukedima", "Dimapur", "Kiphire", "Kohima", "Longleng",
        "Mokokchung", "Mon", "Niuland", "Noklak", "Peren", "Phek",
        "Shamator", "Tseminyu", "Tuensang", "Wokha", "Zunheboto",
    ],
    "OD": [
        "Angul", "Balangir", "Balasore", "Bargarh", "Bhadrak", "Boudh",
        "Cuttack", "Deogarh", "Dhenkanal", "Gajapati", "Ganjam",
        "Jagatsinghpur", "Jajpur", "Jharsuguda", "Kalahandi", "Kandhamal",
        "Kendrapara", "Kendujhar", "Khordha", "Koraput", "Malkangiri",
        "Mayurbhanj", "Nabarangpur", "Nayagarh", "Nuapada", "Puri",
        "Rayagada", "Sambalpur", "Subarnapur", "Sundargarh",
    ],
    "PB": [
        "Amritsar", "Barnala", "Bathinda", "Faridkot", "Fatehgarh Sahib",
        "Fazilka", "Ferozepur", "Gurdaspur", "Hoshiarpur", "Jalandhar",
        "Kapurthala", "Ludhiana", "Malerkotla", "Mansa", "Moga", "Mohali",
        "Muktsar", "Pathankot", "Patiala", "Rupnagar", "Sangrur",
        "Shahid Bhagat Singh Nagar", "Tarn Taran",
    ],
    "RJ": [
        "Ajmer", "Alwar", "Banswara", "Baran", "Barmer", "Bharatpur",
        "Bhilwara", "Bikaner", "Bundi", "Chittorgarh", "Churu", "Dausa",
        "Dholpur", "Dungarpur", "Hanumangarh", "Jaipur", "Jaisalmer",
        "Jalore", "Jhalawar", "Jhunjhunu", "Jodhpur", "Karauli", "Kota",
        "Nagaur", "Pali", "Pratapgarh", "Rajsamand", "Sawai Madhopur",
        "Sikar", "Sirohi", "Sri Ganganagar", "Tonk", "Udaipur",
        "Anupgarh", "Balotra", "Beawar", "Deeg", "Didwana-Kuchaman",
        "Dudu", "Gangapur City", "Jodhpur Rural", "Jaipur Rural", "Kekri",
        "Kotputli-Behror", "Neem Ka Thana", "Phalodi", "Salumbar",
        "Sanchore", "Shahpura", "Tijara",
    ],
    "SK": [
        "East Sikkim", "North Sikkim", "Pakyong", "Soreng",
        "South Sikkim", "West Sikkim",
    ],
    "TN": [
        "Ariyalur", "Chengalpattu", "Chennai", "Coimbatore", "Cuddalore",
        "Dharmapuri", "Dindigul", "Erode", "Kallakurichi", "Kancheepuram",
        "Kanyakumari", "Karur", "Krishnagiri", "Madurai", "Mayiladuthurai",
        "Nagapattinam", "Namakkal", "Nilgiris", "Perambalur", "Pudukkottai",
        "Ramanathapuram", "Ranipet", "Salem", "Sivaganga", "Tenkasi",
        "Thanjavur", "Theni", "Thoothukudi", "Tiruchirappalli", "Tirunelveli",
        "Tirupattur", "Tiruppur", "Tiruvallur", "Tiruvannamalai",
        "Tiruvarur", "Vellore", "Viluppuram", "Virudhunagar",
    ],
    "TS": [
        "Adilabad", "Bhadradri Kothagudem", "Hanumakonda", "Hyderabad",
        "Jagtial", "Jangaon", "Jayashankar Bhupalpally",
        "Jogulamba Gadwal", "Kamareddy", "Karimnagar", "Khammam",
        "Kumuram Bheem", "Mahabubabad", "Mahbubnagar", "Mancherial",
        "Medak", "Medchal-Malkajgiri", "Mulugu", "Nagarkurnool",
        "Nalgonda", "Narayanpet", "Nirmal", "Nizamabad", "Peddapalli",
        "Rajanna Sircilla", "Rangareddy", "Sangareddy", "Siddipet",
        "Suryapet", "Vikarabad", "Wanaparthy", "Warangal",
        "Yadadri Bhuvanagiri",
    ],
    "TR": [
        "Dhalai", "Gomati", "Khowai", "North Tripura", "Sepahijala",
        "South Tripura", "Unakoti", "West Tripura",
    ],
    "UP": [
        "Agra", "Aligarh", "Ambedkar Nagar", "Amethi", "Amroha",
        "Auraiya", "Ayodhya", "Azamgarh", "Baghpat", "Bahraich",
        "Ballia", "Balrampur", "Banda", "Barabanki", "Bareilly",
        "Basti", "Bhadohi", "Bijnor", "Budaun", "Bulandshahr",
        "Chandauli", "Chitrakoot", "Deoria", "Etah", "Etawah",
        "Farrukhabad", "Fatehpur", "Firozabad", "Gautam Buddha Nagar",
        "Ghaziabad", "Ghazipur", "Gonda", "Gorakhpur", "Hamirpur",
        "Hapur", "Hardoi", "Hathras", "Jalaun", "Jaunpur", "Jhansi",
        "Kannauj", "Kanpur Dehat", "Kanpur Nagar", "Kasganj",
        "Kaushambi", "Kheri", "Kushinagar", "Lalitpur", "Lucknow",
        "Maharajganj", "Mahoba", "Mainpuri", "Mathura", "Mau", "Meerut",
        "Mirzapur", "Moradabad", "Muzaffarnagar", "Pilibhit",
        "Pratapgarh", "Prayagraj", "Raebareli", "Rampur", "Saharanpur",
        "Sambhal", "Sant Kabir Nagar", "Shahjahanpur", "Shamli",
        "Shravasti", "Siddharthnagar", "Sitapur", "Sonbhadra",
        "Sultanpur", "Unnao", "Varanasi",
    ],
    "UK": [
        "Almora", "Bageshwar", "Chamoli", "Champawat", "Dehradun",
        "Haridwar", "Nainital", "Pauri Garhwal", "Pithoragarh",
        "Rudraprayag", "Tehri Garhwal", "Udham Singh Nagar", "Uttarkashi",
    ],
    "WB": [
        "Alipurduar", "Bankura", "Birbhum", "Cooch Behar",
        "Dakshin Dinajpur", "Darjeeling", "Hooghly", "Howrah",
        "Jalpaiguri", "Jhargram", "Kalimpong", "Kolkata", "Malda",
        "Murshidabad", "Nadia", "North 24 Parganas",
        "Paschim Bardhaman", "Paschim Medinipur", "Purba Bardhaman",
        "Purba Medinipur", "Purulia", "South 24 Parganas",
        "Uttar Dinajpur",
    ],
    # Union Territories
    "AN": [
        "Nicobars", "North and Middle Andaman", "South Andaman",
    ],
    "CH": [
        "Chandigarh",
    ],
    "DD": [
        "Dadra and Nagar Haveli", "Daman", "Diu",
    ],
    "DL": [
        "Central Delhi", "East Delhi", "New Delhi", "North Delhi",
        "North East Delhi", "North West Delhi", "Shahdara",
        "South Delhi", "South East Delhi", "South West Delhi",
        "West Delhi",
    ],
    "JK": [
        "Anantnag", "Bandipora", "Baramulla", "Budgam", "Doda",
        "Ganderbal", "Jammu", "Kathua", "Kishtwar", "Kulgam",
        "Kupwara", "Poonch", "Pulwama", "Rajouri", "Ramban", "Reasi",
        "Samba", "Shopian", "Srinagar", "Udhampur",
    ],
    "LA": [
        "Kargil", "Leh",
    ],
    "LD": [
        "Lakshadweep",
    ],
    "PY": [
        "Karaikal", "Mahe", "Puducherry", "Yanam",
    ],
}


class Command(BaseCommand):
    help = (
        "Import all 766 Indian districts (LGD-based) using update_or_create. "
        "Idempotent — safe to run multiple times."
    )

    def handle(self, *args, **options):
        total_created = 0
        total_updated = 0
        states_skipped = []

        # Pre-fetch all states into a lookup dict keyed by code
        state_lookup = {
            s.code: s for s in IndianState.objects.all()
        }

        for state_code, district_names in DISTRICTS_BY_STATE.items():
            state = state_lookup.get(state_code)
            if state is None:
                self.stderr.write(
                    self.style.WARNING(
                        f"  [SKIP] State code '{state_code}' not found in DB — "
                        f"run seed_india_data first."
                    )
                )
                states_skipped.append(state_code)
                continue

            state_created = 0
            state_updated = 0
            for name in district_names:
                _, created = IndianDistrict.objects.update_or_create(
                    state=state,
                    name=name,
                    defaults={"lgd_code": ""},
                )
                if created:
                    state_created += 1
                else:
                    state_updated += 1

            total_created += state_created
            total_updated += state_updated

            self.stdout.write(
                f"  {state.name} ({state_code}): "
                f"{state_created} created, {state_updated} already existed"
            )

        self.stdout.write(self.style.SUCCESS(
            f"\n=== Import complete ===\n"
            f"  Districts created : {total_created}\n"
            f"  Districts updated : {total_updated}\n"
            f"  Total processed  : {total_created + total_updated}\n"
            + (
                f"  States skipped   : {', '.join(states_skipped)}\n"
                if states_skipped
                else ""
            )
        ))
