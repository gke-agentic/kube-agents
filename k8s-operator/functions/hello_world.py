import flask

def hello_world(request):
    """Responds to an HTTP request.
    Args:
        request (flask.Request): The request object.
    Returns:
        The response text, or any set of values that can be turned into a
        Response object using `make_response`.
    """
    request_json = request.get_json(silent=True)
    args = request.args

    if request_json and 'name' in request_json:
        name = request_json['name']
    elif args and 'name' in args:
        name = args['name']
    else:
        name = 'World'
    return f'Hello, {name}!'

if __name__ == '__main__':
    app = flask.Flask(__name__)
    app.add_url_rule('/', 'hello_world', hello_world, methods=['GET', 'POST'])
    app.run(debug=True)
