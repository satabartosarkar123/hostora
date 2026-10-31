from __future__ import annotations

import json

from hostora.adapters.llm.base import Route


class FailingClient:
    name: Route = "groq"

    def __init__(self, route_name: Route = "groq") -> None:
        self.name = route_name
        self.calls = 0

    async def complete(self, prompt: str, system: str = "", temperature: float = 0.0) -> str:
        self.calls += 1
        raise RuntimeError(f"Simulated failure on {self.name}")

    async def complete_json(self, system: str, user: str, max_tokens: int) -> str:
        self.calls += 1
        raise RuntimeError(f"Simulated failure on {self.name}")


class MockLlmClient:
    name: Route = "groq"

    def __init__(
        self,
        *,
        candidates_response: dict[str, object] | str | None = None,
        explanation_response: dict[str, object] | str | None = None,
        route_name: Route = "groq",
    ) -> None:
        self.name = route_name
        self._candidates = candidates_response
        self._explanations = explanation_response
        self.recorded_prompts: list[tuple[str, str]] = []

    async def complete(self, prompt: str, system: str = "", temperature: float = 0.0) -> str:
        _ = temperature
        self.recorded_prompts.append((system, prompt))
        prompt_lower = prompt.lower()

        # Verifier prompt
        if "what is usually in" in prompt_lower:
            if "galouti" in prompt_lower:
                return json.dumps({"ingredients": ["mutton", "papaya", "spices"], "diet": "nonveg"})
            if "chicken" in prompt_lower or "biryani" in prompt_lower:
                return json.dumps({"ingredients": ["chicken", "rice", "spices"], "diet": "nonveg"})
            if "pork" in prompt_lower:
                return json.dumps(
                    {"ingredients": ["pork", "barbecue sauce", "spices"], "diet": "nonveg"}
                )
            if "tomato rice" in prompt_lower:
                return json.dumps(
                    {"ingredients": ["rice", "tomato", "cumin", "salt"], "diet": "veg"}
                )
            if "eggless" in prompt_lower:
                return json.dumps(
                    {"ingredients": ["flour", "sugar", "milk", "cocoa"], "diet": "veg"}
                )
            if "chettinad" in prompt_lower:
                return json.dumps(
                    {
                        "ingredients": ["chicken", "coconut", "curry leaves", "spices"],
                        "diet": "nonveg",
                    }
                )
            if "sambar" in prompt_lower or "rice" in prompt_lower:
                return json.dumps(
                    {
                        "ingredients": ["toor dal", "tamarind", "rice", "mustard seeds"],
                        "diet": "veg",
                    }
                )
            if "dhokar dalna" in prompt_lower:
                return json.dumps(
                    {"ingredients": ["chana dal", "ginger", "cumin", "potato"], "diet": "veg"}
                )
            return json.dumps({"ingredients": ["rice", "salt", "water"], "diet": "veg"})

        # Web grounding prompt
        if "search for authentic culinary recipes" in prompt_lower:
            import re

            m = re.search(r"recipes of '([^']+)'", prompt)
            dish = m.group(1) if m else "dish"
            dish_lower = dish.lower()

            if "galouti" in dish_lower or "mutton" in dish_lower:
                return json.dumps(
                    {
                        "ingredients": ["mutton", "papaya", "spices"],
                        "sources": [
                            {
                                "url": "https://en.wikipedia.org/wiki/Galouti_kebab",
                                "quote": (
                                    "Galouti kebab is made from finely minced mutton, "
                                    "raw papaya and aromatic spices."
                                ),
                            }
                        ],
                    }
                )
            if "pork" in dish_lower:
                return json.dumps(
                    {
                        "ingredients": ["pork", "barbecue sauce", "salt"],
                        "sources": [
                            {
                                "url": "https://example.org/pork-ribs",
                                "quote": (
                                    "Pork ribs are prepared from pork cuts cooked in "
                                    "barbecue sauce and seasoned with salt."
                                ),
                            }
                        ],
                    }
                )
            if "chicken" in dish_lower:
                return json.dumps(
                    {
                        "ingredients": ["chicken", "rice", "spices"],
                        "sources": [
                            {
                                "url": "https://example.org/chicken",
                                "quote": (
                                    "Prepared with tender chicken pieces, fragrant rice "
                                    "and aromatic spices."
                                ),
                            }
                        ],
                    }
                )
            if "tomato rice" in dish_lower:
                return json.dumps(
                    {
                        "ingredients": ["rice", "tomato", "cumin", "salt"],
                        "sources": [
                            {
                                "url": "https://example.org/tomato-rice",
                                "quote": (
                                    "Tomato rice is prepared with cooked rice, fresh ripe "
                                    "tomatoes, cumin and salt."
                                ),
                            }
                        ],
                    }
                )
            if "dal makhani" in dish_lower:
                return json.dumps(
                    {
                        "ingredients": ["black lentils", "butter", "cream", "tomato"],
                        "sources": [
                            {
                                "url": "https://example.org/dal-makhani",
                                "quote": (
                                    "Dal makhani is prepared with whole black lentils, "
                                    "butter, fresh cream and ripe tomato."
                                ),
                            }
                        ],
                    }
                )
            if "fresh lime soda" in dish_lower or "lime soda" in dish_lower:
                return json.dumps(
                    {
                        "ingredients": ["lemon", "soda", "sugar", "salt"],
                        "sources": [
                            {
                                "url": "https://example.org/lime-soda",
                                "quote": (
                                    "Fresh lime soda combines freshly squeezed lemon juice, "
                                    "soda water, sugar and salt."
                                ),
                            }
                        ],
                    }
                )

            return json.dumps(
                {
                    "ingredients": ["rice", "salt", "water"],
                    "sources": [
                        {
                            "url": "https://example.org/recipe",
                            "quote": f"{dish} is cooked with rice, salt and boiling water.",
                        }
                    ],
                }
            )

        return "{}"

    async def complete_json(self, system: str, user: str, max_tokens: int) -> str:
        _ = max_tokens
        self.recorded_prompts.append((system, user))
        if "explanation" in system.lower():
            if isinstance(self._explanations, str):
                return self._explanations
            if self._explanations is not None:
                return json.dumps(self._explanations)
            return json.dumps(
                {
                    "explanations": {
                        "starter": "Sized for predicted guests across starters.",
                        "main": "Sized for predicted guests across main courses.",
                        "rice": "Sized for predicted guests across rice.",
                        "bread": "Sized for predicted guests across breads.",
                        "side": "Sized for predicted guests across side dishes.",
                        "dessert": "Sized for predicted guests across desserts.",
                        "drink": "Sized for predicted guests across beverages.",
                        "supply": "Sized for predicted guests across supplies.",
                        "decor": "Sized for venue scale.",
                    }
                }
            )

        if isinstance(self._candidates, str):
            return self._candidates
        if self._candidates is not None:
            return json.dumps(self._candidates)

        user_lower = user.lower()

        # Regional branching for proposals
        if "kolkata" in user_lower or "bengal" in user_lower:
            items = [
                {
                    "name": "Kolkata Mutton Biryani",
                    "local_names": ["কলকাতা মাটন বিরিয়ানি"],
                    "category": "food",
                    "course": "main",
                    "serving_kind": "rice_main",
                    "ingredients": ["mutton", "rice", "potato", "spices"],
                    "declared_diet": "nonveg",
                    "why": ("Iconic Kolkata celebration centerpiece with tender mutton and potato"),
                    "search_query": "kolkata mutton biryani",
                    "price_estimate": [0.45, 0.70],
                },
                {
                    "name": "Dhokar Dalna",
                    "local_names": ["ধোঁকার ডালনা"],
                    "category": "food",
                    "course": "side",
                    "serving_kind": "main_gravy",
                    "ingredients": ["chana dal", "ginger", "potato", "cumin"],
                    "declared_diet": "veg",
                    "why": "Traditional Bengali spiced lentil cake curry",
                    "search_query": "dhokar dalna",
                    "price_estimate": [0.25, 0.40],
                },
                {
                    "name": "Sandesh",
                    "local_names": ["সন্দেশ"],
                    "category": "food",
                    "course": "dessert",
                    "serving_kind": "sweet_piece",
                    "ingredients": ["chenna", "sugar", "cardamom"],
                    "declared_diet": "veg",
                    "why": "Classic Bengali sweet made from fresh cottage cheese",
                    "search_query": "bengali sandesh",
                    "price_estimate": [0.30, 0.50],
                },
                {
                    "name": "Aam Panna",
                    "local_names": ["আম পান্না"],
                    "category": "beverage",
                    "course": "drink",
                    "serving_kind": "drink_cup",
                    "ingredients": ["raw mango", "sugar", "cumin", "mint"],
                    "declared_diet": "vegan",
                    "why": "Refreshing tangy welcome drink",
                    "search_query": "aam panna",
                    "price_estimate": [0.15, 0.25],
                },
            ]
        elif "chennai" in user_lower or "tamil" in user_lower:
            items = [
                {
                    "name": "Chettinad Chicken",
                    "local_names": ["செட்டிநாடு சிக்கன்"],
                    "category": "food",
                    "course": "main",
                    "serving_kind": "main_gravy",
                    "ingredients": ["chicken", "coconut", "curry leaves", "peppercorn"],
                    "declared_diet": "nonveg",
                    "why": "Fiery and aromatic traditional Chettinad wedding specialty",
                    "search_query": "chettinad chicken",
                    "price_estimate": [0.40, 0.65],
                },
                {
                    "name": "Kalyana Sambar",
                    "local_names": ["கல்யாண சாம்பார்"],
                    "category": "food",
                    "course": "side",
                    "serving_kind": "main_gravy",
                    "ingredients": ["toor dal", "drumstick", "tamarind", "spices"],
                    "declared_diet": "veg",
                    "why": "Authentic Tamil wedding lentil stew with freshly ground sambar podi",
                    "search_query": "kalyana sambar",
                    "price_estimate": [0.20, 0.35],
                },
                {
                    "name": "Elaneer Payasam",
                    "local_names": ["இளநீர் பாயாசம்"],
                    "category": "food",
                    "course": "dessert",
                    "serving_kind": "dessert_liquid",
                    "ingredients": ["tender coconut", "milk", "condensed milk", "cardamom"],
                    "declared_diet": "veg",
                    "why": "Cooling tender coconut dessert popular across South Indian feasts",
                    "search_query": "elaneer payasam",
                    "price_estimate": [0.35, 0.55],
                },
                {
                    "name": "Madras Filter Coffee",
                    "local_names": ["மதராஸ் பில்டர் காபி"],
                    "category": "beverage",
                    "course": "drink",
                    "serving_kind": "drink_cup",
                    "ingredients": ["coffee chicory", "milk", "sugar"],
                    "declared_diet": "veg",
                    "why": "Freshly brewed iconic decoction beverage",
                    "search_query": "filter coffee decoction",
                    "price_estimate": [0.15, 0.25],
                },
            ]
        else:
            items = [
                {
                    "name": "Tomato Rice",
                    "local_names": ["Tamatar Chawal"],
                    "category": "food",
                    "course": "main",
                    "serving_kind": "rice_main",
                    "ingredients": ["rice", "tomato", "cumin", "salt"],
                    "declared_diet": "veg",
                    "why": "Delicate tomato and herb rice",
                    "search_query": "tomato rice",
                    "price_estimate": [0.20, 0.35],
                },
                {
                    "name": "Dal Makhani",
                    "local_names": ["दाल मखनी"],
                    "category": "food",
                    "course": "side",
                    "serving_kind": "main_gravy",
                    "ingredients": ["black lentils", "butter", "cream", "tomato"],
                    "declared_diet": "veg",
                    "why": "Slow-cooked rich creamy lentil main",
                    "search_query": "dal makhani",
                    "price_estimate": [0.25, 0.45],
                },
                {
                    "name": "Fresh Lime Soda",
                    "local_names": ["नींबू सोडा"],
                    "category": "beverage",
                    "course": "drink",
                    "serving_kind": "drink_cup",
                    "ingredients": ["lemon", "soda", "sugar", "salt"],
                    "declared_diet": "vegan",
                    "why": "Sparkling zesty welcome drink",
                    "search_query": "fresh lime soda",
                    "price_estimate": [0.10, 0.20],
                },
            ]

        # Adversarial or brief checks: if brief mentions pork
        if "pork" in user_lower:
            items.append(
                {
                    "name": "Pork Ribs",
                    "local_names": [],
                    "category": "food",
                    "course": "starter",
                    "serving_kind": "starter_piece",
                    "ingredients": ["pork", "barbecue sauce", "salt"],
                    "declared_diet": "nonveg",
                    "why": "Requested in prompt",
                    "search_query": "pork ribs",
                    "price_estimate": [0.5, 0.8],
                }
            )

        if "chicken biryani" in user_lower:
            items.insert(
                0,
                {
                    "name": "Chicken Biryani",
                    "local_names": ["चिकन बिरयानी"],
                    "category": "food",
                    "course": "main",
                    "serving_kind": "rice_main",
                    "ingredients": ["chicken", "rice", "spices"],
                    "declared_diet": "nonveg",
                    "why": "Popular chicken rice main",
                    "search_query": "chicken biryani",
                    "price_estimate": [0.4, 0.6],
                },
            )

        return json.dumps({"items": items})
