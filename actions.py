# -*- coding: utf-8 -*-
from typing import Text, Dict, Any, List, Union

from rasa_sdk.events import SlotSet
from rasa_sdk import Action, Tracker

from constants import schema
from graph_database import GraphDatabase


def resolve_mention(tracker: Tracker) -> Text:
    """
    Resolves a mention of an entity, such as first, to the actual entity.
    If multiple entities are listed during the conversation, the entities
    are stored in the slot 'entities' as an list. We resolve the mention,
    such as first, to the list index and retrieve the actual entity.

    :param tracker: tracker
    :return: name of the actually entity
    """
    graph_database = GraphDatabase()

    mention = tracker.get_slot("mention")
    entities = tracker.get_slot("entities")

    if mention is not None and entities is not None:
        idx = int(graph_database.lookup("mention-lookup", mention))

        if type(idx) is int and idx < len(entities):
            return entities[idx]


def get_entity_type(tracker: Tracker) -> Text:
    """
    Get the entity type mentioned by the user. As the user may speak of an
    entity type in plural, we need to map the mentioned entity type to the
    type used in the knowledge base.

    :param tracker: tracker
    :return: entity type (same type as used in the knowledge base)
    """
    graph_database = GraphDatabase()
    entity_type = tracker.get_slot("entity_type")
    return graph_database.lookup("entity-type-lookup", entity_type)


def get_attribute(tracker: Tracker) -> Text:
    """
    Get the attribute mentioned by the user. As the user may use a synonym for
    an attribute, we need to map the mentioned attribute to the
    attribute name used in the knowledge base.

    :param tracker: tracker
    :return: attribute (same type as used in the knowledge base)
    """
    graph_database = GraphDatabase()
    attribute = tracker.get_slot("attribute")
    return graph_database.lookup("attribute-lookup", attribute)


def get_entity_name(tracker: Tracker, entity_type: Text):
    """
    Get the name of the entity the user referred to. Either the NER detected the
    entity and stored its name in the corresponding slot or the user referred to
    the entity by an ordinal number, such as first or last. In that case, the
    entity mention needs to be resolved.

    :param tracker: Tracker
    :param entity_type: the entity type
    :return: the name of the actual entity (value of key attribute in the knowledge base)
    """
    name = tracker.get_slot(entity_type)
    if name is None:
        name = resolve_mention(tracker)
    return name


def to_str(entity: Dict[Text, Any], entity_keys: Union[Text, List[Text]]) -> Text:
    """
    Converts an entity to a string by concatenating the values of the provided
    entity keys.

    :param entity: the entity with all its attributes
    :param entity_keys: the name of the key attributes
    :return: a string that represents the entity
    """
    if isinstance(entity_keys, str):
        entity_keys = [entity_keys]

    v_list = []
    for key in entity_keys:
        _e = entity
        for k in key.split("."):
            _e = _e[k]

        if "balance" in key or "amount" in key:
            v_list.append(f"{str(_e)} €")
        elif "date" in key:
            v_list.append(_e.strftime("%d.%m.%Y (%H:%M:%S)"))
        else:
            v_list.append(str(_e))
    return ", ".join(v_list)


class ActionQueryEntities(Action):
    """Action for listing entities.
    The entities might be restricted by specific attributes."""

    def name(self):
        return "action_query_entities"

    def run(self, dispatcher, tracker, domain):
        graph_database = GraphDatabase()

        # first need to know the entity type we are looking for
        entity_type = get_entity_type(tracker)

        if entity_type is None:
            dispatcher.utter_template("utter_rephrase", tracker)
            return []

        # check what attributes the NER found for entity type
        attributes = []
        if entity_type in schema:
            for attr in schema[entity_type]["attributes"]:
                attr_val = tracker.get_slot(attr)
                if attr_val is not None:
                    attributes.append({"key": attr, "value": attr_val})

        # query knowledge base
        entities = graph_database.get_entities(entity_type, attributes)

        # filter out transactions that do not belong the set account (if any)
        if entity_type == "transaction":
            account_number = tracker.get_slot("account")
            entities = self._filter_transaction_entities(entities, account_number)

        if not entities:
            dispatcher.utter_template(
                "I could not find any entities for '{}'.".format(entity_type), tracker
            )
            return []

        # utter a response that contains all found entities
        # use the 'representation' attributes to print an entity
        entity_representation = schema[entity_type]["representation"]

        dispatcher.utter_message(
            "Found the following '{}' entities:".format(entity_type)
        )
        for i, e in enumerate(entities):
            representation_string = to_str(e, entity_representation)
            dispatcher.utter_message(f"{i + 1}: {representation_string}")

        # set slots
        # set the entities slot in order to resolve references to one of the found
        # entites later on
        entity_key = schema[entity_type]["key"]

        slots = [
            SlotSet("entity_type", entity_type),
            SlotSet("entities", list(map(lambda x: to_str(x, entity_key), entities))),
        ]

        # if only one entity was found, that the slot of that entity type to the
        # found entity
        if len(entities) == 1:
            slots.append(SlotSet(entity_type, to_str(entities[0], entity_key)))

        return slots

    def _filter_transaction_entities(
        self, entities: List[Dict[Text, Any]], account_number: Text
    ) -> List[Dict[Text, Any]]:
        """
        Filter out all transactions that do not belong to the provided account number.

        :param entities: list of transactions
        :param account_number: account number
        :return: list of filtered transactions
        """
        if account_number is not None:
            filtered_entities = []
            for entity in entities:
                if entity["account-of-creator"]["account-number"] == account_number:
                    filtered_entities.append(entity)
            return filtered_entities

        return entities


class ActionQueryAttribute(Action):
    """Action for querying a specific attribute of an entity."""

    def name(self):
        return "action_query_attribute"

    def run(self, dispatcher, tracker, domain):
        graph_database = GraphDatabase()

        # get entity type of entity
        entity_type = get_entity_type(tracker)

        if entity_type is None:
            dispatcher.utter_template("utter_rephrase", tracker)

        # get name of entity and attribute of interest
        name = get_entity_name(tracker, entity_type)
        attribute = get_attribute(tracker)

        if name is None or attribute is None:
            dispatcher.utter_template("utter_rephrase", tracker)
            return [SlotSet(entity_type, None)]

        # query knowledge base
        key_attribute = schema[entity_type]["key"]
        value = graph_database.get_attribute_of(
            entity_type, key_attribute, name, attribute
        )

        # utter response
        if value is not None and len(value) == 1:
            dispatcher.utter_message(
                f"{name} has the value '{value[0]}' for attribute '{attribute}'."
            )
        else:
            dispatcher.utter_message(
                f"Did not found a valid value for attribute {attribute} for entity '{name}'."
            )

        # reset slot of entity type, because TODO
        return [SlotSet(entity_type, None)]


class ActionCompareEntities(Action):
    """Action for comparing multiple entities."""

    def name(self):
        return "action_compare_entities"

    def run(self, dispatcher, tracker, domain):
        graph = GraphDatabase()

        # get entities to compare and their entity type
        entities = tracker.get_slot("entities")
        entity_type = get_entity_type(tracker)

        if entities is None or entity_type is None:
            dispatcher.utter_template("utter_rephrase", tracker)
            return []

        # get attribute of interest
        attribute = get_attribute(tracker)

        if attribute is None:
            dispatcher.utter_template("utter_rephrase", tracker)
            return []

        # utter response for every entity that shows the value of the attribute
        for e in entities:
            key_attribute = schema[entity_type]["key"]
            value = graph.get_attribute_of(entity_type, key_attribute, e, attribute)

            if value is not None and len(value) == 1:
                dispatcher.utter_message(
                    f"{e} has the value '{value[0]}' for attribute '{attribute}'."
                )

        return []


class ActionResolveEntity(Action):
    """Action for resolving a mention."""

    def name(self):
        return "action_resolve_entity"

    def run(self, dispatcher, tracker, domain):
        entity_type = tracker.get_slot("entity_type")
        entities = tracker.get_slot("entities")

        # Check if NER recognized entity directly
        # (e.g. bank name was mentioned and recognized as 'bank')
        value = tracker.get_slot(entity_type)
        if value is not None and value in entities:
            return [SlotSet(entity_type, value)]

        # Check if entity was mentioned as 'first', 'second', etc.
        value = resolve_mention(tracker)
        if value is not None:
            return [SlotSet(entity_type, value)]

        dispatcher.utter_message("Sorry, I didn't get that.")

        return [SlotSet(entity_type, None)]
