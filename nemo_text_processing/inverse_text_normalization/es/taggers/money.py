# Copyright (c) 2021, NVIDIA CORPORATION & AFFILIATES.  All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import pynini
from pynini.lib import pynutil

from nemo_text_processing.inverse_text_normalization.es.utils import get_abs_path
from nemo_text_processing.text_normalization.en.graph_utils import (
    INPUT_CASED,
    INPUT_LOWER_CASED,
    NEMO_DIGIT,
    NEMO_SIGMA,
    GraphFst,
    capitalized_input_graph,
    convert_space,
    delete_extra_space,
    delete_space,
    insert_space,
)


class MoneyFst(GraphFst):
    """
    Finite state transducer for classifying money
        e.g. doce dólares y cinco céntimos -> money { integer_part: "12" fractional_part: 05 currency: "$" }

    Args:
        cardinal: CardinalFst
        decimal: DecimalFst
        input_case: accepting either "lower_cased" or "cased" input.
    """

    def __init__(self, cardinal: GraphFst, decimal: GraphFst, input_case: str = INPUT_LOWER_CASED):
        super().__init__(name="money", kind="classify")
        # quantity, integer_part, fractional_part, currency

        cardinal_graph = cardinal.graph_no_exception
        graph_decimal_final = decimal.final_graph_wo_negative

        unit_singular = pynini.string_file(get_abs_path("data/money/currency_major_singular.tsv"))
        unit_singular = pynini.invert(unit_singular)
        unit_plural = pynini.string_file(get_abs_path("data/money/currency_major_plural.tsv"))
        unit_plural = pynini.invert(unit_plural)

        unit_minor_singular = pynini.string_file(get_abs_path("data/money/currency_minor_singular.tsv"))
        unit_minor_singular = pynini.invert(unit_minor_singular)
        unit_minor_plural = pynini.string_file(get_abs_path("data/money/currency_minor_plural.tsv"))
        unit_minor_plural = pynini.invert(unit_minor_plural)

        if input_case == INPUT_CASED:
            unit_singular = capitalized_input_graph(unit_singular)
            unit_singular_capitalized = pynini.string_file(get_abs_path("data/money/currency_major_singular_capitalized.tsv"))
            unit_singular |= pynini.invert(unit_singular_capitalized).optimize()
            
            unit_plural = capitalized_input_graph(unit_plural)
            unit_plural_capitalized = pynini.string_file(get_abs_path("data/money/currency_major_plural_capitalized.tsv"))
            unit_plural |= pynini.invert(unit_plural_capitalized).optimize()

            unit_minor_singular = capitalized_input_graph(unit_minor_singular).optimize()
            unit_minor_plural = capitalized_input_graph(unit_minor_plural).optimize()

        graph_unit_singular = pynutil.insert("currency: \"") + convert_space(unit_singular) + pynutil.insert("\"")
        graph_unit_plural = pynutil.insert("currency: \"") + convert_space(unit_plural) + pynutil.insert("\"")

        graph_unit_minor_singular = (
            pynutil.insert("currency: \"") + convert_space(unit_minor_singular) + pynutil.insert("\"")
        )
        graph_unit_minor_plural = (
            pynutil.insert("currency: \"") + convert_space(unit_minor_plural) + pynutil.insert("\"")
        )

        add_leading_zero_to_double_digit = (NEMO_DIGIT + NEMO_DIGIT) | (pynutil.insert("0") + NEMO_DIGIT)

        one_graph = pynini.union("un", "una").optimize()
        if input_case == INPUT_CASED:
            one_graph |= pynini.union("Un", "Una").optimize()

        # twelve dollars (and) fifty cents, zero cents
        cents_standalone = (
            pynutil.insert("morphosyntactic_features: \",\"")  # always use a comma in the decimal
            + insert_space
            + pynutil.insert("fractional_part: \"")
            + pynini.union(
                pynutil.add_weight(((NEMO_SIGMA - one_graph) @ cardinal_graph), -0.7) @ add_leading_zero_to_double_digit
                + delete_space,
                pynini.cross(one_graph, "01") + delete_space,
            )
            + pynutil.insert("\"")
        )

        and_graph = pynini.union("con", "y").optimize()
        if input_case == INPUT_CASED:
            and_graph |= pynini.union("Con", "Y").optimize()

        optional_cents_standalone = pynini.closure(
            delete_space
            + pynini.closure(pynutil.delete(and_graph) + delete_space, 0, 1)
            + insert_space
            + cents_standalone
            + pynutil.delete(pynini.union(unit_minor_singular, unit_minor_plural)),
            0,
            1,
        )

        # twelve dollars fifty, only after integer
        # setenta y cinco dólares con sesenta y tres~$75,63
        optional_cents_suffix = pynini.closure(
            delete_extra_space
            + pynutil.insert("morphosyntactic_features: \",\"")  # always use a comma in the decimal
            + insert_space
            + pynutil.insert("fractional_part: \"")
            + pynini.closure(pynutil.delete(pynini.union("con", "Con")) + delete_space, 0, 1)
            + pynutil.add_weight(cardinal_graph @ add_leading_zero_to_double_digit, -0.7)
            + pynutil.insert("\""),
            0,
            1,
        )

        graph_integer = (
            pynutil.insert("integer_part: \"")
            + ((NEMO_SIGMA - one_graph) @ cardinal_graph)
            + pynutil.insert("\"")
            + delete_extra_space
            + graph_unit_plural
            + (optional_cents_standalone | optional_cents_suffix)
        )
        graph_integer |= (
            pynutil.insert("integer_part: \"")
            + pynini.cross(one_graph, "1")
            + pynutil.insert("\"")
            + delete_extra_space
            + graph_unit_singular
            + (optional_cents_standalone | optional_cents_suffix)
        )

        cents_only_int = pynutil.insert("integer_part: \"0\" ")
        cents_only_units = graph_unit_minor_singular | graph_unit_minor_plural
        cents_only_graph = cents_only_int + cents_standalone + pynini.accep(" ") + cents_only_units

        graph_decimal = (graph_decimal_final + delete_extra_space + graph_unit_plural) | cents_only_graph
        graph_decimal |= graph_decimal_final + pynutil.delete(" de") + delete_extra_space + graph_unit_plural
        final_graph = graph_integer | graph_decimal
        final_graph = self.add_tokens(final_graph)
        self.fst = final_graph.optimize()
