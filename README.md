# CLX

Another implementation of the Coding-Academy Lecture Manager eXperimental.


## Helpful information

To install clx, create a virtualenv, activate it, then install all four packages:

    python -m pip install  clx-common
    python -m pip install  clx
    python -m pip install  clx-cli
    python -m pip install  clx-faststream-backend

Before using the clx conversion, you have to start up a RabbitMQ queue, e.g. using Docker:

    docker run -p 5672:5672 rabbitmq:latest


## Useless Information

The current version is `0.1.0` (Included to make bumpversion happy.)

Eventually there will be information how to build and deploy CLX here.
