"""
The fault-injection proxy: the fourth place failures come from.

Day 6 seeds SERVICE bugs in the API. Day 9's plans can carry TEST bugs. This
package produces ENVIRONMENT failures -- and, from Day 12, FLAKES. Four
categories, four separate injection sites, which is what makes the Day 14
accuracy figure meaningful rather than circular.

Like the harness, this package never imports `api`. It speaks HTTP to whatever
address it is pointed at.
"""