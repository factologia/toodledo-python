from datetime import date, datetime
from functools import partial
from json import dump, dumps, load
from logging import debug, error

from marshmallow import fields, post_load, pre_load, Schema
from marshmallow.validate import Length
from requests_oauthlib import OAuth2Session

# all the fields have the option for not being set
# dates have an extra option of being 0 or a real date

# states for this field are:
# a GMT timestamp with the time set to noon
# unset, represented by API as 0
class ToodledoDate(fields.Field):
	def _serialize(self, value, attr, obj):
		if value is None:
			return 0
		return datetime(year=value.year, month=value.month, day=value.day).timestamp()

	def _deserialize(self, value, attr, obj):
		if value == 0:
			return None
		return date.fromtimestamp(float(value))

# states for this field are:
# a GMT timestamp
# unset, represented by API as 0
class ToodledoDatetime(fields.Field):
	def _serialize(self, value, attr, obj):
		if value is None:
			return 0
		return value.timestamp()

	def _deserialize(self, value, attr, obj):
		if value == 0:
			return None
		return datetime.fromtimestamp(float(value))

class ToodledoTags(fields.Field):
	def _serialize(self, value, attr, obj):
		assert isinstance(value, list)
		return ", ".join(sorted(value))

	def _deserialize(self, value, attr, obj):
		assert isinstance(value, str)
		if value == "":
			return []
		return [x.strip() for x in value.split(",")]

class Task:
	def __init__(self, **data):
		for name, item in data.items():
			setattr(self, name, item)

	def __repr__(self):
		attributes = sorted(["{}={}".format(name, item) for name, item in self.__dict__.items()])
		return "<Task {}>".format(", ".join(attributes))

	def IsComplete(self):
		return self.completedDate is not None

class ToodledoError(Exception):
	errorCodeToMessage = {
		  1: "No access token was given",
		  2: "The access token was invalid",
		  3: "Too many API requests",
		  4: "The API is offline for maintenance",
		601: "Your task must have a title.",
		602: "Only 50 tasks can be added/edited/deleted at a time.",
		603: "The maximum number of tasks allowed per account (20000) has been reached",
		604: "Empty id",
		605: "Invalid task",
		606: "Nothing was added/edited. You'll get this error if you attempt to edit a task but don't pass any parameters to edit.",
		607: "Invalid folder id",
		608: "Invalid context id",
		609: "Invalid goal id",
		610: "Invalid location id",
		611: "Malformed request",
		612: "Invalid parent id",
		613: "Incorrect field parameters",
		614: "Parent was deleted",
		615: "Invalid collaborator",
		616: "Unable to reassign or share task"
	}

	def __init__(self, errorCode):
		errorMessage = ToodledoError.errorCodeToMessage.get(errorCode, "Unknown error")
		super().__init__(errorMessage, errorCode)

class TaskSchema(Schema):
	id_ = fields.Integer(dump_to="id", load_from="id")
	title = fields.String(validate=Length(max=255))
	tags = ToodledoTags(dump_to="tag", load_from="tag")
	startDate = ToodledoDate(dump_to="startdate", load_from="startdate")
	dueDate = ToodledoDate(dump_to="duedate", load_from="duedate")
	modified = ToodledoDatetime()
	completedDate = ToodledoDate(dump_to="completed", load_from="completed")

	@post_load
	def MakeTask(self, data):
		return Task(**data)

class Account:
	def __init__(self, lastEditTask, lastDeleteTask):
		self.lastEditTask = lastEditTask
		self.lastDeleteTask = lastDeleteTask

	def __repr__(self):
		return "<AccountInfo lastEditTask={}, lastDeleteTask={}>".format(self.lastEditTask, self.lastDeleteTask)

class AccountSchema(Schema):
	lastEditTask = ToodledoDatetime(dump_to="lastedit_task", load_from="lastedit_task")
	lastDeleteTask = ToodledoDatetime(dump_to="lastdelete_task", load_from="lastdelete_task")

	@post_load
	def MakeAccount(self, data):
		return Account(data["lastEditTask"], data["lastDeleteTask"])

def DumpTaskList(taskList):
	# TODO - pass many=True to the schema instead of this custom stuff
	schema = TaskSchema()
	return [schema.dump(task).data for task in taskList]

def GetAccount(session):
	accountInfo = session.get(Toodledo.getAccountUrl)
	accountInfo.raise_for_status()
	return AccountSchema().load(accountInfo.json()).data

def GetTasks(session, params):
	allTasks = []
	limit = 1000 # single request limit
	start = 0
	while True:
		debug("Start: {}".format(start))
		params["start"] = start
		params["num"] = limit
		response = session.get(Toodledo.getTasksUrl, params=params)
		response.raise_for_status()
		tasks = response.json()
		if "errorCode" in tasks:
			error("Toodledo error: {}".format(tasks))
			raise ToodledoError(tasks["errorCode"])
		# the first field contains the count or the error code
		allTasks.extend(tasks[1:])
		debug("Retrieved {} tasks".format(len(tasks[1:])))
		if len(tasks[1:]) < limit:
			break
		start += limit
	schema = TaskSchema()
	return [schema.load(x).data for x in allTasks]

def EditTasks(session, taskList):
	if len(taskList) == 0:
		return
	debug("Total tasks to edit: {}".format(len(taskList)))
	limit = 50 # single request limit
	start = 0
	while True:
		debug("Start: {}".format(start))
		listDump = DumpTaskList(taskList[start: start + limit])
		response = session.post(Toodledo.editTasksUrl, params={"tasks":dumps(listDump)})
		response.raise_for_status()
		debug("Response: {},{}".format(response, response.text))
		taskResponse = response.json()
		if "errorCode" in taskResponse:
			raise ToodledoError(taskResponse["errorCode"])
		if len(taskList[start: start + limit]) < limit:
			break
		start += limit

def AddTasks(session, taskList):
	if len(taskList) == 0:
		return
	limit = 50 # single request limit
	start = 0
	while True:
		debug("Start: {}".format(start))
		listDump = DumpTaskList(taskList[start: start + limit])
		response = session.post(Toodledo.addTasksUrl, params={"tasks":dumps(listDump)})
		response.raise_for_status()
		if "errorCode" in response.json():
			raise ToodledoError(tasks["errorCode"])
		if len(taskList[start: start + limit]) < limit:
			break
		start += limit

def DeleteTasks(session, taskList):
	if len(taskList) == 0:
		return
	taskIdList = [task.id_ for task in taskList]
	limit = 50 # single request limit
	start = 0
	while True:
		debug("Start: {}".format(start))
		response = session.post(Toodledo.deleteTasksUrl, params={"tasks":dumps(taskIdList[start: start + limit])})
		response.raise_for_status()
		if "errorCode" in response.json():
			raise ToodledoError(tasks["errorCode"])
		if len(taskIdList[start: start + limit]) < limit:
			break
		start += limit

class Toodledo:
	tokenUrl = "https://api.toodledo.com/3/account/token.php"
	getAccountUrl = "https://api.toodledo.com/3/account/get.php"
	getTasksUrl = "https://api.toodledo.com/3/tasks/get.php"
	deleteTasksUrl = "https://api.toodledo.com/3/tasks/delete.php"
	addTasksUrl = "https://api.toodledo.com/3/tasks/add.php"
	editTasksUrl = "https://api.toodledo.com/3/tasks/edit.php"

	def __init__(self, clientId, clientSecret, tokenStorage, scope):
		self.tokenStorage = tokenStorage
		self.clientId = clientId
		self.clientSecret = clientSecret
		self.scope = scope
		self.session = self.Session()

	def TokenSaver(self, token):
		with open(self.tokenStorage, "w") as f:
			dump(token, f)

	def Authorize(self):
		authorizationBaseUrl = "https://api.toodledo.com/3/account/authorize.php"
		session = OAuth2Session(client_id=self.clientId, scope=self.scope)
		authorizationUrl, _ = session.authorization_url(authorizationBaseUrl)
		print("Go to the following URL and authorize the app:" + authorizationUrl)

		try:
			from pyperclip import copy
			copy(authorizationUrl)
			print("URL copied to clipboard")
		except ImportError:
			pass

		redirectResponse = input("Paste the full redirect URL here:")

		token = session.fetch_token(Toodledo.tokenUrl, client_secret=self.clientSecret, authorization_response=redirectResponse, token_updater=self.TokenSaver)
		self.TokenSaver(token)
		return token

	def Session(self):
		try:
			with open(self.tokenStorage, "r") as f:
				token = load(f)
		except FileNotFoundError:
			token = self.Authorize()

		return OAuth2Session(
			client_id=self.clientId,
			token=token,
			auto_refresh_kwargs={"client_id": self.clientId, "client_secret": self.clientSecret},
			auto_refresh_url=Toodledo.tokenUrl,
			token_updater=self.TokenSaver)

	def ReauthorizeIfNecessary(self, func):
		try:
			return func(self.session)
		except TokenMissingError:
			# this can happen if the refresh token has expired
			self.session = self.Authorize()
			return func(self.session)

	def GetAccount(self):
		self.ReauthorizeIfNecessary(partial(GetAccount))

	def GetTasks(self, params):
		self.ReauthorizeIfNecessary(partial(GetTasks, params=params))

	def EditTasks(self, params):
		self.ReauthorizeIfNecessary(partial(EditTasks, params=params))

	def AddTasks(self, params):
		self.ReauthorizeIfNecessary(partial(AddTasks, params=params))

	def DeleteTasks(self, params):
		self.ReauthorizeIfNecessary(partial(DeleteTasks, params=params))
