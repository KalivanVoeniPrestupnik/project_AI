import requests


url = "http://10.28.250.29:8000"

def create_group(group: Group):
    response = requests.post(url + "/groups/group", json= group.__dict__)
    if response.status_code == 200:
        return response.json()["id"]
    return None

def create_user(user:User):
    response = requests.post(url+"/groups/user", json= user.__dict__)
    if response.status_code == 200:
        return response.json()["id"]
    return None

class User():
    def __init__(self, name:str, tg_id:int):
        self.name = name
        self.tg_id = tg_id

class Group():
    def __init__(self, name:str, description:str):
        self.name = name
        self.description = description

for i in range(5):
    create_group(Group(name="group"+str(i), description="description"+str(i)))
# for i in range(5):
#     create_user(User(name=f"user{i}", tg_id=i))