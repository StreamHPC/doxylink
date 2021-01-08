from typing import Tuple

from pyparsing import Word, Literal, nums, alphanums, OneOrMore, Optional,\
    SkipTo, ParseException, Group, Combine, delimitedList, quotedString,\
    nestedExpr, ParseResults, oneOf, ungroup, Keyword

# define punctuation - reuse of expressions helps packratting work better
LPAR, RPAR, LBRACK, RBRACK, COMMA, EQ = map(Literal, "()[],=")

# Qualifier to go in front of type in the argument list (unsigned const int foo)
qualifier = OneOrMore(Keyword('const') ^ Keyword('typename') ^ Keyword('struct') ^ Keyword('enum'))
qualifier = ungroup(qualifier.addParseAction(' '.join))


def turn_parseresults_to_list(s, loc, toks):
    return ParseResults(normalise_templates(toks[0].asList()))


def normalise_templates(toks):
    s_list = ['<']
    s_list_append = s_list.append  # lookup append func once, instead of many times
    for tok in toks:
        if isinstance(tok, str):  # See if it's a string
            s_list_append(' ' + tok)
        else:
            # If it's not a string
            s_list_append(normalise_templates(tok))
    s_list_append(' >')
    return ''.join(s_list)


# Skip pairs of brackets.
angle_bracket_pair = nestedExpr(opener='<', closer='>').setParseAction(turn_parseresults_to_list)
# TODO Fix for nesting brackets
parentheses_pair = LPAR + SkipTo(RPAR) + RPAR
square_bracket_pair = LBRACK + SkipTo(RBRACK) + RBRACK

# TODO I guess this should be a delimited list (by '::') of name and angle brackets
nonfundamental_input_type = Combine(Word(alphanums + ':_') + Optional(angle_bracket_pair + Optional(Word(alphanums + ':_'))))
fundamental_input_type = OneOrMore(Keyword('bool') ^ Keyword('short') ^ Keyword('int') ^ Keyword('long') ^ Keyword('signed') ^ Keyword('unsigned') ^ Keyword('char') ^ Keyword('float') ^ Keyword('double'))
input_type = fundamental_input_type ^ nonfundamental_input_type

# A number. e.g. -1, 3.6 or 5
number = Word('-.' + nums)

# The name of the argument. We will ignore this but it must be matched anyway.
input_name = OneOrMore(Word(alphanums + '_') | angle_bracket_pair | parentheses_pair | square_bracket_pair)

# Grab the '&', '*' or '**' type bit in (const QString & foo, int ** bar)
pointer_or_reference = Combine(oneOf('* &') + Optional('...'))

# The '=QString()' or '=false' bit in (int foo = 4, bool bar = false)
default_value = Literal('=') + OneOrMore(number | quotedString | input_type | parentheses_pair | angle_bracket_pair | square_bracket_pair | Word('|&^'))

# A combination building up the interesting bit -- the argument type, e.g. 'const QString &', 'int' or 'char*'
argument_type = Optional(qualifier, default='')("qualifier") + \
                input_type('input_type').setParseAction(' '.join) + \
                Optional(pointer_or_reference, default='')("pointer_or_reference1") + \
                Optional('const')('const_pointer_or_reference') + \
                Optional(pointer_or_reference, default='')("pointer_or_reference2")

# Argument + variable name + default
argument = Group(argument_type('argument_type') + Optional(input_name) + Optional(default_value))

# List of arguments in parentheses with an optional 'const' on the end
arglist = LPAR + delimitedList(argument)('arg_list') + Optional(COMMA + '...')('var_args') + RPAR


def normalise(symbol: str) -> Tuple[str, str]:
    """
    Takes a c++ symbol or function and splits it into symbol and a normalised argument list.

    :Parameters:
        symbol : string
            A C++ symbol or function definition like ``PolyVox::Volume``, ``Volume::printAll() const``

    :return:
        a tuple consisting of two strings: ``(qualified function name or symbol, normalised argument list)``
    """

    try:
        bracket_location = symbol.index('(')
        # Split the input string into everything before the opening bracket and everything else
        function_name = symbol[:bracket_location]
        arglist_input_string = symbol[bracket_location:]
    except ValueError:
        # If there's no brackets, then there's no function signature. This means the passed in symbol is just a type name
        return symbol, ''

    # This is a very common signature so we'll make a special case for it. It requires no parsing anyway
    if arglist_input_string.startswith('()'):
        arglist_input_string_no_spaces = arglist_input_string.replace(' override', '').replace(' final', '').replace(' ', '')
        if arglist_input_string_no_spaces in ('()', '()=0', '()=default'):
            return function_name, '()'
        elif arglist_input_string_no_spaces in ('()const', '()const=0'):
            return function_name, '() const'

    # By now we're left with something like "(blah, blah)", "(blah, blah) const" or "(blah, blah) const =0"
    try:
        closing_bracket_location = arglist_input_string.rindex(')')
        arglist_suffix = arglist_input_string[closing_bracket_location + 1:]
        arglist_input_string = arglist_input_string[:closing_bracket_location + 1]
    except ValueError:
        # This shouldn't happen.
        print('Could not find closing bracket in %s' % arglist_input_string)
        raise

    try:
        result = arglist.parseString(arglist_input_string)
    except ParseException:
        raise
    else:
        # Will be a list or normalised string arguments
        # e.g. ['OBMol&', 'vector< int >&', 'OBBitVec&', 'OBBitVec&', 'int', 'int']
        normalised_arg_list = []

        # Cycle through all the matched arguments
        for arg in result.arg_list:
            # Here is where we build up our normalised form of the argument
            argument_string_list = ['']
            if arg.qualifier:
                argument_string_list.append(''.join((arg.qualifier, ' ')))
            argument_string_list.append(arg.input_type)

            # Functions can have a funny combination of *, & and const between the type and the name so build up a list of those here:
            const_pointer_ref_list = []
            const_pointer_ref_list.append(arg.pointer_or_reference1)
            if arg.const_pointer_or_reference:
                const_pointer_ref_list.append(''.join((' ', arg.const_pointer_or_reference, ' ')))
            # same here
            const_pointer_ref_list.append(arg.pointer_or_reference2)
            # And combine them into a single normalised string and add them to the argument list
            argument_string_list.extend(const_pointer_ref_list)

            # Finally we join our argument string and add it to our list
            normalised_arg_list.append(''.join(argument_string_list))

        # If the function contains a variable number of arguments (int foo, ...) then add them on.
        if result.var_args:
            normalised_arg_list.append('...')

        # Combine all the arguments and put parentheses around it
        normalised_arg_list_string = ''.join(['(', ', '.join(normalised_arg_list), ')'])

        # Add a const onto the end
        if 'const' in arglist_suffix:
            normalised_arg_list_string += ' const'

        return function_name, normalised_arg_list_string

    # TODO Maybe this should raise an exception?
    return None
