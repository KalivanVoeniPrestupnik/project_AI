__id__ = "my_packet_s_packetami"
__name__ = "Помошник"
__version__ = "1.0"
__author__ = "@Sharashkinsss"
__description__ = "Помощь под рукой"
__min_version__ = "11.9.0"

from typing import Any
import requests
from base_plugin import BasePlugin, HookResult, HookStrategy
from mandre_lib import Mandre, Color
from ui.bulletin import BulletinHelper
from mandre_lib import MandreUI
from ui.settings import Header, Text, Switch, Divider, Input




class User():
    """
    Класс пользователя
    """
    def __init__(self, name:str, tg_id:int):
        self.name = name
        self.tg_id = tg_id
class Group():
    """
    Класс групп
    """
    def __init__(self, name:str, description:str):
        self.name = name
        self.description = description
        self.users = []
class CloudBase():
    """
    Класс облачной базы данных
    """
    def __init__(self,plugin:BasePlugin):
        """
        Метод получения url из настроек плагина
        :param plugin:
        """
        self.url=plugin.get_setting("url", "http://10.28.250.29:8000")
    def create_user(self, user:User):
        """
        Метод создания пользователя
        :param user:
        :return id:
        """
        response = requests.post(self.url+"/groups/user", json= user.__dict__)
        if response.status_code == 200:
            return response.json()["id"]
        return None

    def create_group(self, group:Group):
        """
        Метод создания пользователя
        :param group:
        :return id:
        """
        response = requests.post(self.url+"/groups/group", json=group.__dict__)
        if response.status_code == 200:
            return response.json()["id"]
        return None
    def get_users(self, group_id):
        """
        Метод получения всех пользователей по group_id
        :param group_id:
        :return json:
        """
        response = requests.get(self.url+"/groups/users", params={"group_id": group_id})
        if response.status_code == 200:
            return response.json()
        return None
    def get_groups(self):
        """
        Метод получения всех групп
        :return json:
        """
        response = requests.get(self.url+"/groups/groups")
        if response.status_code == 200:
            return response.json()
        return None
    def link_user_to_group(self,user_id:int, group_id:int):
        """
        Метод присоединения пользователя к группе
        :param user_id, group_id:
        :return str:
        """
        response = requests.post(self.url+"/groups/link_to_group",json={"group_id": group_id, "user_id": user_id})
        if response.status_code == 200:
            return "Успешное поключение"
        return None
    def delete_user(self,user_id:int):
        """
        Метод удаления пользователя
        :param user_id:
        :return str:
        """
        response = requests.post(self.url+"/groups/delete/user",json={"user_id": user_id})
        if response.status_code == 200:
            return "Пользователь удалён"
        return None

    def delete_group(self, group_id: int):
        """
        Метод удаления группы
        :param group_id:
        :return str:
        """
        response = requests.post(self.url + "/groups/delete/group", json={"group_id": group_id})
        if response.status_code == 200:
            return "Группа успешно удалена"
        return None

    def delete_link(self, group_id: int, user_id: int):
        """
        Метод удаления пользователя из группы
        :param user_id, group_id:
        :return str:
        """
        response = requests.post(self.url + "/groups/delete/user", json={"group_id": group_id, "user_id": user_id})
        if response.status_code == 200:
            return "Успешно кикнут с сервера за гнилой базар"
        return None

class AITEACHER(BasePlugin):
    def on_plugin_load(self):

        Mandre.use_persistent_storage(self)

        self.add_on_send_message_hook()

        items = [
            {
                "text": "Основные настройки",
                "icon": "msg_settings",
                "on_click": lambda: self.switch_tab(0)
            },
            {
                "text": "Группы и пользователи",
                "icon": "msg_storage",
                "on_click": lambda: self.switch_tab(1)
            },
        ]
        MandreUI.setup_settings_bottom_bar(
            plugin_instance=self,
            items=items,
            active_index_key="current_tab",
            bg_color=Color.argb(210, 50, 50, 55),
            active_color=Color.WHITE,
            inactive_color=Color.rgb(140, 140, 140)
        )

        Mandre.register_command(self, "add", self.add_user2group_cmd)
        Mandre.register_command(self, "del", self.delete_user_from_group_cmd)
        Mandre.register_command(self, "user", self.create_user_cmd)



        self.cloud = CloudBase(self)


    def switch_tab(self, index):
        """Переключение вкладки"""
        self.set_setting("current_tab", index)
        Mandre.apply_and_refresh_settings(self)

    def on_send_message_hook(self, account: int, params: Any) -> HookResult:
        return Mandre.handle_outgoing_command(params) or HookResult()

    def show_user(self):
        MandreUI.show(
            title="Выберите действие",
            items=["Первое", "Второе", "Третье"],
            on_select=lambda index, text: BulletinHelper.show_success(f"Вы выбрали: {text}"),
            message="Какой вариант вам нравится?",
            cancel_text="Отмена"
        )


    def create_settings(self):
        tab = self.get_setting("current_tab", 1)

        if tab == 0:  # Основные
            return [
                Header(text="Основные настройки"),
                Input(
                    key="url_input_key",
                    text="Сменить аддрес",
                    default=self.get_setting(key="url", default="0.0.0.0"),
                    subtext="Текущий аддрес: " + str(self.get_setting(key="url", default="0.0.0.0")),
                    icon="msg_text",
                    on_change=self._change_url
                ),
                Divider(),
                Text(
                    text= "f'<a href=https://ollama.com/search>{text}</a>",

                )
            ]
        elif tab == 1:  # Данные
            return [
                Header(text="Добавление пользователя"),
                Text(
                    text="группа",
                    icon="msg_select",
                    on_click=lambda _: self.switch_tab(2),
                ),
            ]
        elif tab == 2:  #
            for i in range(5):
                return [
                    Header(text="Добавление пользователей"),
                    Text(
                        text="Выбрать чат",
                        icon="msg_select",
                        on_click=lambda _: self.show_user(),
                    ),
                ]
        return None

    def _change_url(self,url:str):
        self.set_setting(key="url", value=url)
        Mandre.apply_and_refresh_settings(self)

    def add_user2group_cmd(self, plugin, args:str, params):

        try:
            params.message=str(self.cloud.link_user_to_group(user_id = params.peer , group_id = int(args)))
        except Exception as e:
            params.message=f"\n {args[4:]}"
        # BulletinHelper.show_error()
        return HookResult(strategy=HookStrategy.MODIFY, params=params)

    def delete_user_from_group_cmd(self, plugin, args: str, params):

        try:

            params.message=str(self.cloud.get_groups())
        except Exception as e:
            params.message = str(e)
        # BulletinHelper.show_error()
        return HookResult(strategy=HookStrategy.MODIFY, params=params)

    def create_user_cmd(self, plugin, args: str, params):

        try:
            # params.message = str(self.cloud.link_user_to_group(*map(int, params.message[4:].split())))
            params.message=str(self.cloud.create_user(User(args[5:], params.peer)))
        except Exception as e:
            params.message = str(e)
        # BulletinHelper.show_error()
        return HookResult(strategy=HookStrategy.MODIFY, params=params)
